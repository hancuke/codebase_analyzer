from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO
 


_SCHEMA_VERSION = 4
_STATE_NAME = "state.json"
_TEMP_PREFIX = ".state-"

class WorkbenchError(Exception):
    """Invalid, incompatible, busy, or damaged workbench storage."""


def _validate_keys(keys: Iterable[str]) -> None:
    keys = tuple(keys)
    prefixes: dict[str, str] = {}
    for key in keys:
        if (
            type(key) is not str or not key or "\\" in key or "\x00" in key
            or key.startswith("/") or re.match(r"^[A-Za-z]:", key)
            or any(part in {"", ".", ".."} for part in key.split("/"))
        ):
            raise ValueError(f"invalid document_key: {key!r}")
        parts = key.split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            previous = prefixes.setdefault(prefix.casefold(), prefix)
            if previous != prefix:
                raise ValueError(f"case-insensitive path collision: {key!r}")
    files = {key.casefold() for key in keys}
    for key in keys:
        parts = key.split("/")
        if any(
            "/".join(parts[:index]).casefold() in files
            for index in range(1, len(parts))
        ):
            raise ValueError(f"file/directory collision: {key!r}")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


class Workbench:
    """Store successful document results; callers reanalyze inputs on every run."""

    def __init__(
        self, root: Path, lock_file: BinaryIO,
        completed: dict[str, tuple[int, str]],
    ) -> None:
        self._root = root
        self._lock_file = lock_file
        self._completed = completed
        self._closed = False
        self._failed = False

    @classmethod
    def open(
        cls,
        root: str | os.PathLike[str],
        *,
        protected_roots: Iterable[str | os.PathLike[str]] = (),
    ) -> Workbench:
        """Create or reopen a result store outside caller-protected directories."""
        root = Path(root).absolute()
        if isinstance(protected_roots, (str, os.PathLike)):
            raise TypeError("protected_roots must be an iterable of directory paths")
        resolved_root = root.resolve()
        for path in protected_roots:
            protected = Path(path).resolve()
            if (
                resolved_root == protected
                or resolved_root in protected.parents
                or protected in resolved_root.parents
            ):
                raise ValueError(
                    f"workbench must not overlap a protected directory: {protected}"
                )
        if not root.exists() and not root.is_symlink():
            root.mkdir(parents=True, exist_ok=True)
        cls._ensure_directory(root)
        lock_file = cls._acquire_lock(root)
        try:
            legacy = root / "manifest.json"
            if legacy.exists() or legacy.is_symlink():
                raise WorkbenchError("legacy schema: finish this batch with the previous version")
            state = root / _STATE_NAME
            temporaries = []
            for path in root.iterdir():
                if path.name in {".lock", _STATE_NAME, "outputs"}:
                    continue
                if path.name.startswith(_TEMP_PREFIX) and path.name.endswith(".tmp"):
                    cls._ensure_file(path)
                    temporaries.append(path)
                else:
                    raise WorkbenchError(f"unexpected workbench data: {path.name}")
            if state.exists() or state.is_symlink():
                cls._ensure_file(state)
                outputs = root / "outputs"
                if outputs.exists() or outputs.is_symlink():
                    cls._ensure_directory(outputs)
                try:
                    value = json.loads(state.read_bytes(), object_pairs_hook=_unique_object)
                    completed = cls._decode_state(value)
                except ValueError as error:
                    raise WorkbenchError("invalid workbench state") from error
                instance = cls(root, lock_file, completed)
                instance._verify_results()
                return instance
            outputs = root / "outputs"
            if outputs.exists() or outputs.is_symlink():
                raise WorkbenchError("workbench has outputs but no state")
            for path in temporaries:
                path.unlink()
            instance = cls(root, lock_file, {})
            instance._write_state({})
            return instance
        except BaseException:
            cls._release_lock(lock_file)
            raise

    def contains(self, document_key: str) -> bool:
        """Return whether a complete, valid result has been committed for this key."""
        self._ensure_open()
        if not isinstance(document_key, str):
            raise TypeError("document_key must be a string")
        _validate_keys((document_key,))
        record = self._completed.get(document_key)
        if record is None:
            return False
        self._read_output(document_key, record)
        return True

    def save(self, document_key: str, content: bytes) -> None:
        """Save a full result before atomically recording success."""
        self._ensure_open()
        if not isinstance(document_key, str):
            raise TypeError("document_key must be a string")
        _validate_keys((*self._completed, document_key))
        if document_key in self._completed:
            raise WorkbenchError(f"document is already completed: {document_key!r}")
        if not isinstance(content, bytes):
            raise TypeError("document result must be bytes")
        path = self._output_path(document_key, create=True)
        if path.exists() or path.is_symlink():
            self._ensure_file(path)
        with path.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        parent = path.parent
        while parent != self._root:
            self._fsync_directory(parent)
            parent = parent.parent
        updated = dict(self._completed)
        updated[document_key] = (len(content), hashlib.sha256(content).hexdigest())
        try:
            self._write_state(updated)
        except BaseException:
            # Replacement may have succeeded before the directory sync failed.
            self._failed = True
            raise
        self._completed = updated

    def results(self) -> Mapping[str, bytes]:
        """Return all committed results in key order, with integrity checks."""
        self._ensure_open()
        return MappingProxyType({
            key: self._read_output(key, record)
            for key, record in sorted(self._completed.items())
        })

    def publish(self, document_root: str | os.PathLike[str]) -> None:
        """Copy committed results to their destinations, then close and clean up."""
        self._ensure_open()
        target_root = Path(document_root).absolute()
        source = self._root.resolve()
        target = target_root.resolve()
        if source == target or source in target.parents:
            raise ValueError("document_root must be outside the workbench")
        outputs = self.results()
        for key in outputs:
            destination = (target_root / key).resolve()
            if destination == source or source in destination.parents:
                raise ValueError("published document must not overwrite the workbench")
        target_root.mkdir(parents=True, exist_ok=True)
        self._ensure_directory(target_root)
        for key, content in outputs.items():
            destination = target_root / key
            parent = target_root
            for part in destination.parent.relative_to(target_root).parts:
                parent = parent / part
                if not parent.exists() and not parent.is_symlink():
                    parent.mkdir()
                self._ensure_directory(parent)
            if destination.exists() or destination.is_symlink():
                self._ensure_file(destination)
            descriptor, name = tempfile.mkstemp(prefix=".publish-", dir=parent)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(content)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
                current = parent
                while True:
                    self._fsync_directory(current)
                    if current == target_root:
                        break
                    current = current.parent
            finally:
                temporary.unlink(missing_ok=True)
        self.close()
        # Delete only committed files; unexpected leftovers prevent final removal.
        for key in outputs:
            path = self._output_path(key)
            self._ensure_file(path)
            path.unlink()
        output_root = self._root / "outputs"
        if output_root.exists():
            for directory, _, _ in os.walk(output_root, topdown=False, followlinks=False):
                self._ensure_directory(Path(directory))
                Path(directory).rmdir()
        for path in self._root.glob(f"{_TEMP_PREFIX}*.tmp"):
            self._ensure_file(path)
            path.unlink()
        (self._root / _STATE_NAME).unlink()
        (self._root / ".lock").unlink()
        self._root.rmdir()

    def _verify_results(self) -> None:
        for key, record in self._completed.items():
            self._read_output(key, record)

    def _output_path(self, key: str, *, create: bool = False) -> Path:
        self._ensure_directory(self._root)
        path = self._root / "outputs" / key
        parent = self._root
        for part in path.parent.relative_to(self._root).parts:
            parent = parent / part
            if create and not parent.exists() and not parent.is_symlink():
                parent.mkdir()
            self._ensure_directory(parent)
        return path

    def _read_output(self, key: str, record: tuple[int, str]) -> bytes:
        path = self._output_path(key)
        self._ensure_file(path)
        content = path.read_bytes()
        if (len(content), hashlib.sha256(content).hexdigest()) != record:
            raise WorkbenchError(f"result failed integrity check: {key!r}")
        return content

    @staticmethod
    def _decode_state(value: object) -> dict[str, tuple[int, str]]:
        if not isinstance(value, dict):
            raise ValueError("state must be an object")
        if (
            type(value.get("schema_version")) is not int
            or value["schema_version"] != _SCHEMA_VERSION
        ):
            raise WorkbenchError(f"unsupported workbench schema: {value.get('schema_version')!r}")
        if set(value) != {"schema_version", "completed"}:
            raise ValueError("invalid state fields")
        completed = value["completed"]
        if not isinstance(completed, dict):
            raise ValueError("invalid completions")
        _validate_keys(completed)
        records = {}
        for key, record in completed.items():
            if not isinstance(record, dict) or set(record) != {"size_bytes", "sha256"}:
                raise ValueError("invalid completion record")
            size, digest = record["size_bytes"], record["sha256"]
            if (
                type(size) is not int or size < 0
                or not isinstance(digest, str) or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ValueError("invalid completion record")
            records[key] = (size, digest)
        return records

    def _write_state(self, completed: dict[str, tuple[int, str]]) -> None:
        state = {
            "schema_version": _SCHEMA_VERSION,
            "completed": {
                key: {"size_bytes": size, "sha256": digest}
                for key, (size, digest) in sorted(completed.items())
            },
        }
        self._ensure_directory(self._root)
        target = self._root / _STATE_NAME
        if target.exists() or target.is_symlink():
            self._ensure_file(target)
        descriptor, name = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=".tmp", dir=self._root)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=True, allow_nan=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            self._fsync_directory(self._root)
        finally:
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _ensure_directory(path: Path) -> None:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError as error:
            raise WorkbenchError(f"directory is missing: {path}") from error
        if not stat.S_ISDIR(mode):
            raise WorkbenchError(f"path must be a real directory: {path}")

    @staticmethod
    def _ensure_file(path: Path) -> None:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError as error:
            raise WorkbenchError(f"result or state is missing: {path}") from error
        if not stat.S_ISREG(mode):
            raise WorkbenchError(f"path must be a regular file, not a symlink: {path}")

    def _ensure_open(self) -> None:
        if self._closed:
            raise WorkbenchError("workbench is closed")
        if self._failed:
            raise WorkbenchError("commit failed; close and reopen the workbench")

    def close(self) -> None:
        if not self._closed:
            try:
                self._release_lock(self._lock_file)
            finally:
                self._closed = True

    def __enter__(self) -> Workbench:
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    @staticmethod
    def _acquire_lock(root: Path) -> BinaryIO:
        path = root / ".lock"
        if path.exists() or path.is_symlink():
            Workbench._ensure_file(path)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise WorkbenchError("workbench lock must not be a symlink") from error
            raise
        lock_file = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            if not stat.S_ISREG(os.fstat(lock_file.fileno()).st_mode):
                raise WorkbenchError("workbench lock must be a regular file")
            _lock_file(lock_file)
        except OSError as error:
            lock_file.close()
            if isinstance(error, BlockingIOError) or error.errno in {
                errno.EACCES, errno.EAGAIN, errno.EDEADLK,
            }:
                raise WorkbenchError(f"workbench is already open: {root}") from error
            raise
        except BaseException:
            lock_file.close()
            raise
        return lock_file

    @staticmethod
    def _release_lock(lock_file: BinaryIO) -> None:
        try:
            _unlock_file(lock_file)
        finally:
            lock_file.close()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if not hasattr(os, "O_DIRECTORY"):
            return
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


if os.name == "nt":
    import msvcrt

    def _lock_file(lock_file: BinaryIO) -> None:
        lock_file.seek(0)
        if lock_file.read(1) == b"":
            lock_file.seek(0)
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(lock_file: BinaryIO) -> None:
        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _lock_file(lock_file: BinaryIO) -> None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(lock_file: BinaryIO) -> None:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
