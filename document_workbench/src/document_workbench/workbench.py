from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from document_updater import DocumentResult, ResultKind, apply_results


_SCHEMA_VERSION = 7
_STATE_NAME = "state.json"
_TEMP_PREFIX = ".state-"

class WorkbenchError(Exception):
    """Invalid, incompatible, closed, or damaged workbench storage."""


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


def _validate_task_id(task_id: str) -> None:
    if not isinstance(task_id, str):
        raise TypeError("task_id must be a string")
    if not task_id or task_id != task_id.strip():
        raise ValueError("task_id must be non-empty without outer whitespace")


@dataclass(frozen=True)
class TaskResult:
    """One immutable checkpoint identified by a document/task pair."""

    document_key: str
    task_id: str
    results: tuple[DocumentResult, ...]

    def __post_init__(self) -> None:
        _validate_keys((self.document_key,))
        _validate_task_id(self.task_id)
        if not isinstance(self.results, tuple) or any(
            not isinstance(result, DocumentResult) for result in self.results
        ):
            raise TypeError("task results must be a tuple of DocumentResult")
        for result in self.results:
            if (
                not isinstance(result.fragment_id, str)
                or not isinstance(result.kind, ResultKind)
                or (result.markdown is not None and not isinstance(result.markdown, str))
            ):
                raise ValueError("invalid fragment result fields")

    def to_dict(self) -> dict[str, object]:
        """Return the complete checkpoint as JSON-compatible fields."""
        return {
            "document_key": self.document_key,
            "task_id": self.task_id,
            "results": [
                {
                    "fragment_id": result.fragment_id,
                    "kind": result.kind.value,
                    "markdown": result.markdown,
                }
                for result in self.results
            ],
        }

    @classmethod
    def from_dict(cls, value: object) -> TaskResult:
        """Restore a checkpoint with strict field and domain validation."""
        if not isinstance(value, dict) or set(value) != {
            "document_key", "task_id", "results"
        }:
            raise ValueError("invalid task result fields")
        key, task_id, items = value["document_key"], value["task_id"], value["results"]
        if not isinstance(key, str) or not isinstance(task_id, str):
            raise ValueError("task identifiers must be text")
        if not isinstance(items, list):
            raise ValueError("task results must be a list")
        results = []
        for item in items:
            if not isinstance(item, dict) or set(item) != {
                "fragment_id", "kind", "markdown"
            }:
                raise ValueError("invalid fragment result fields")
            fragment_id, kind, markdown = item["fragment_id"], item["kind"], item["markdown"]
            if (
                not isinstance(fragment_id, str)
                or not isinstance(kind, str)
                or (markdown is not None and not isinstance(markdown, str))
            ):
                raise ValueError("invalid fragment result field types")
            results.append(DocumentResult(fragment_id, ResultKind(kind), markdown))
        return cls(key, task_id, tuple(results))


class Workbench:
    """Store successful results without locks or concurrent-access detection.

    Callers own exclusive access to the store and publication directory, and
    reanalyze unchanged inputs on each recovery attempt.
    """

    def __init__(
        self, root: Path,
        completed: dict[str, tuple[int, str]],
    ) -> None:
        self._root = root
        self._completed = completed
        self._tasks: dict[str, dict[str, TaskResult]] = {}
        self._frozen = False
        self._closed = False
        self._failed = False

    @classmethod
    def open(
        cls,
        root: str | os.PathLike[str],
        *,
        protected_roots: Iterable[str | os.PathLike[str]] = (),
    ) -> Workbench:
        """Create or reopen a caller-exclusive store outside protected directories."""
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
        legacy = root / "manifest.json"
        if legacy.exists() or legacy.is_symlink():
            raise WorkbenchError("legacy schema: finish this batch with the previous version")
        state = root / _STATE_NAME
        temporaries = []
        for path in root.iterdir():
            if path.name == ".lock":
                # Schema 5 stores from the locking implementation remain readable.
                cls._ensure_file(path)
                continue
            if path.name in {_STATE_NAME, "outputs"}:
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
            instance = cls(root, completed)
            instance._frozen = bool(completed)
            if value["schema_version"] == _SCHEMA_VERSION:
                instance._tasks = {
                    key: {
                        task_id: TaskResult.from_dict(checkpoint)
                        for task_id, checkpoint in results.items()
                    }
                    for key, results in value["tasks"].items()
                }
                instance._frozen = value["frozen"]
            instance._verify_results()
            return instance
        outputs = root / "outputs"
        if outputs.exists() or outputs.is_symlink():
            raise WorkbenchError("workbench has outputs but no state")
        for path in temporaries:
            path.unlink()
        instance = cls(root, {})
        instance._write_state({})
        return instance

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

    def save(self, document_key: str, content: str) -> None:
        """Save a full result before atomically recording success."""
        self._ensure_open()
        if not isinstance(document_key, str):
            raise TypeError("document_key must be a string")
        _validate_keys(set(self._completed) | set(self._tasks) | {document_key})
        if document_key in self._completed:
            raise WorkbenchError(f"document is already completed: {document_key!r}")
        if not isinstance(content, str):
            raise TypeError("document result must be text")
        encoded = content.encode("utf-8")
        if not self._frozen:
            self._commit_state(self._completed, frozen=True)
        path = self._output_path(document_key, create=True)
        if path.exists() or path.is_symlink():
            self._ensure_file(path)
        with path.open("wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        parent = path.parent
        while parent != self._root:
            self._fsync_directory(parent)
            parent = parent.parent
        updated = dict(self._completed)
        updated[document_key] = (len(encoded), hashlib.sha256(encoded).hexdigest())
        self._commit_state(updated)

    @staticmethod
    def _validate_task_id(task_id: str) -> None:
        _validate_task_id(task_id)

    def contains_task(self, document_key: str, task_id: str) -> bool:
        """Return whether this document/task pair has committed fragment results."""
        self._ensure_open()
        _validate_keys((document_key,))
        self._validate_task_id(task_id)
        return task_id in self._tasks.get(document_key, {})

    def save_task(
        self, document_key: str, task_id: str, results: tuple[DocumentResult, ...],
    ) -> None:
        """Atomically checkpoint ordered fragments before document assembly."""
        self._ensure_open()
        _validate_keys(set(self._completed) | set(self._tasks) | {document_key})
        self._validate_task_id(task_id)
        if self._frozen:
            raise WorkbenchError("task collection is frozen; no further tasks may be saved")
        if self.contains_task(document_key, task_id):
            raise WorkbenchError("task is already completed")
        checkpoint = TaskResult(document_key, task_id, results)
        tasks = {key: dict(values) for key, values in self._tasks.items()}
        tasks.setdefault(document_key, {})[task_id] = checkpoint
        self._commit_state(self._completed, tasks=tasks)

    def task_results(self, document_key: str) -> Mapping[str, tuple[DocumentResult, ...]]:
        """Return ordered immutable task results, or an empty mapping."""
        self._ensure_open()
        _validate_keys((document_key,))
        return MappingProxyType({
            task_id: checkpoint.results
            for task_id, checkpoint in sorted(self._tasks.get(document_key, {}).items())
        })

    def task_documents(self) -> tuple[str, ...]:
        """Return document keys with committed tasks in deterministic order."""
        self._ensure_open()
        return tuple(sorted(self._tasks))

    def assemble(self, document_root: str | os.PathLike[str]) -> None:
        """Merge task documents after the caller confirms generation is complete.

        Task IDs sort lexically; fragment order within each task is preserved.
        Missing originals are empty documents. Saved documents are verified and
        skipped on retries. Originals are never modified by assembly.
        """
        self._ensure_open()
        keys = self.task_documents()
        target_root = self._document_root(document_root, keys)
        if target_root.exists() or target_root.is_symlink():
            self._ensure_directory(target_root)
        for key in keys:
            if self.contains(key):
                continue
            path = target_root / key
            parent = target_root
            for part in path.parent.relative_to(target_root).parts:
                parent = parent / part
                if parent.exists() or parent.is_symlink():
                    self._ensure_directory(parent)
                else:
                    break
            original = ""
            if path.exists() or path.is_symlink():
                self._ensure_file(path)
                original = path.read_text(encoding="utf-8")
            results = tuple(
                result
                for task_results in self.task_results(key).values()
                for result in task_results
            )
            self.save(key, apply_results(original, results))

    def _document_root(
        self, document_root: str | os.PathLike[str], keys: Iterable[str],
    ) -> Path:
        target_root = Path(document_root).absolute()
        source = self._root.resolve()
        target = target_root.resolve()
        if source == target or source in target.parents:
            raise ValueError("document_root must be outside the workbench")
        for key in keys:
            destination = (target_root / key).resolve()
            if destination == source or source in destination.parents:
                raise ValueError("published document must not overwrite the workbench")
        return target_root

    def _commit_state(
        self, completed: dict[str, tuple[int, str]], *,
        tasks: dict[str, dict[str, TaskResult]] | None = None,
        frozen: bool | None = None,
    ) -> None:
        try:
            self._write_state(completed, tasks=tasks, frozen=frozen)
        except BaseException:
            # Replacement may have succeeded before directory synchronization.
            self._failed = True
            raise
        self._completed = completed
        if tasks is not None:
            self._tasks = tasks
        if frozen is not None:
            self._frozen = frozen

    def results(self) -> Mapping[str, str]:
        """Return all committed results in key order, with integrity checks."""
        self._ensure_open()
        return MappingProxyType({
            key: self._read_output(key, record)
            for key, record in sorted(self._completed.items())
        })

    def publish(self, document_root: str | os.PathLike[str]) -> None:
        """Assemble and publish after the caller confirms generation is complete.

        All task documents are saved before publication begins. Retries reuse
        saved documents without reading or merging their originals again.
        """
        self._ensure_open()
        if set(self._tasks) - set(self._completed):
            self.assemble(document_root)
        outputs = self.results()
        target_root = self._document_root(document_root, outputs)
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
            encoded = content.encode("utf-8")
            descriptor, name = tempfile.mkstemp(prefix=".publish-", dir=parent)
            temporary = Path(name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
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
        self._cleanup()

    def _cleanup(self) -> None:
        outputs = self.results()
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
        legacy_lock = self._root / ".lock"
        if legacy_lock.exists() or legacy_lock.is_symlink():
            self._ensure_file(legacy_lock)
            legacy_lock.unlink()
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

    def _read_output(self, key: str, record: tuple[int, str]) -> str:
        path = self._output_path(key)
        self._ensure_file(path)
        encoded = path.read_bytes()
        if (len(encoded), hashlib.sha256(encoded).hexdigest()) != record:
            raise WorkbenchError(f"result failed integrity check: {key!r}")
        try:
            return encoded.decode("utf-8")
        except UnicodeDecodeError as error:
            raise WorkbenchError(f"result is not valid UTF-8: {key!r}") from error

    @staticmethod
    def _decode_state(value: object) -> dict[str, tuple[int, str]]:
        if not isinstance(value, dict):
            raise ValueError("state must be an object")
        if (
            type(value.get("schema_version")) is not int
            or value["schema_version"] not in {5, _SCHEMA_VERSION}
        ):
            raise WorkbenchError(f"unsupported workbench schema: {value.get('schema_version')!r}")
        fields = {"schema_version", "completed"}
        if value["schema_version"] == _SCHEMA_VERSION:
            fields |= {"tasks", "frozen"}
        if set(value) != fields:
            raise ValueError("invalid state fields")
        completed = value["completed"]
        if not isinstance(completed, dict):
            raise ValueError("invalid completions")
        _validate_keys(completed)
        if value["schema_version"] == _SCHEMA_VERSION:
            tasks = value["tasks"]
            if not isinstance(tasks, dict) or type(value["frozen"]) is not bool:
                raise ValueError("invalid task state")
            _validate_keys(set(completed) | set(tasks))
            for key, results in tasks.items():
                if not isinstance(results, dict) or not results:
                    raise ValueError("invalid task results")
                for task_id, serialized in results.items():
                    Workbench._validate_task_id(task_id)
                    checkpoint = TaskResult.from_dict(serialized)
                    if (checkpoint.document_key, checkpoint.task_id) != (key, task_id):
                        raise ValueError("task checkpoint identity mismatch")
            if completed and not value["frozen"]:
                raise ValueError("completed documents require frozen tasks")
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

    def _write_state(
        self, completed: dict[str, tuple[int, str]], *,
        tasks: dict[str, dict[str, TaskResult]] | None = None,
        frozen: bool | None = None,
    ) -> None:
        state = {
            "schema_version": _SCHEMA_VERSION,
            "completed": {
                key: {"size_bytes": size, "sha256": digest}
                for key, (size, digest) in sorted(completed.items())
            },
            "tasks": {
                key: {
                    task_id: checkpoint.to_dict()
                    for task_id, checkpoint in sorted(results.items())
                }
                for key, results in sorted((self._tasks if tasks is None else tasks).items())
            },
            "frozen": self._frozen if frozen is None else frozen,
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
        """Invalidate this object without publishing or removing saved results."""
        self._closed = True

    def __enter__(self) -> Workbench:
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if not hasattr(os, "O_DIRECTORY"):
            return
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
