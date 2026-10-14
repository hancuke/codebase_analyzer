from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

from .errors import (
    InvalidUnitIdError,
    WorkbenchBusyError,
    WorkbenchCorruptError,
    WorkbenchStateError,
    UnsupportedWorkbenchVersionError,
)
from .models import UnitCompletion, WorkbenchPhase, WorkbenchStatus


_SCHEMA_VERSION = 1
_MANIFEST_NAME = "manifest.json"
_LOCK_NAME = ".lock"
_INPUTS_NAME = "inputs"
_MANIFEST_TEMP_PREFIX = ".manifest-"
_MANIFEST_TEMP_SUFFIX = ".tmp"
_OUTPUTS_NAME = "outputs"
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_HASH_LENGTH = 64


class DocumentRun:
    """Persistent checkpoints for one fixed-input document update run."""

    def __init__(
        self,
        root: Path,
        lock_file: BinaryIO,
        manifest: dict[str, Any],
    ) -> None:
        self._root = root
        self._lock_file = lock_file
        self._manifest = manifest
        self._closed = False

    @classmethod
    def open_or_create(
        cls,
        root: str | os.PathLike[str],
        *,
        capture_inputs: Callable[[Path], None],
    ) -> DocumentRun:
        workbench_root = Path(root).absolute()
        workbench_root.mkdir(parents=True, exist_ok=True)
        cls._ensure_real_directory(workbench_root, "workbench root")
        lock_file = cls._acquire_lock(workbench_root)
        try:
            manifest_path = workbench_root / _MANIFEST_NAME
            if manifest_path.exists():
                manifest = cls._read_manifest(workbench_root)
                instance = cls(workbench_root, lock_file, manifest)
                instance._validate_persisted_data()
                return instance

            if manifest_path.is_symlink():
                raise WorkbenchCorruptError("workbench manifest must not be a symlink")
            reserved_names = {
                _LOCK_NAME,
                _INPUTS_NAME,
            }
            manifest_temporaries = tuple(
                path
                for path in workbench_root.iterdir()
                if path.name.startswith(_MANIFEST_TEMP_PREFIX)
                and path.name.endswith(_MANIFEST_TEMP_SUFFIX)
            )
            unexpected = sorted(
                path.name
                for path in workbench_root.iterdir()
                if path.name not in reserved_names and path not in manifest_temporaries
            )
            if unexpected:
                raise WorkbenchCorruptError(
                    "workbench has data but no manifest; refusing to initialize: "
                    + ", ".join(unexpected)
                )
            cls._remove_uncommitted_inputs(workbench_root, manifest_temporaries)

            inputs_root = workbench_root / _INPUTS_NAME
            inputs_root.mkdir()
            capture_inputs(inputs_root)
            inventory = cls._inventory_tree(inputs_root, sync=True)
            cls._fsync_tree_directories(inputs_root)
            cls._fsync_directory(workbench_root)
            manifest = {
                "schema_version": _SCHEMA_VERSION,
                "phase": WorkbenchPhase.SNAPSHOT_READY.value,
                "inputs": inventory,
                "unit_ids": None,
                "completions": {},
            }
            cls._write_manifest(workbench_root, manifest)
            return cls(workbench_root, lock_file, manifest)
        except BaseException:
            cls._release_lock(lock_file)
            raise

    @classmethod
    def open(cls, root: str | os.PathLike[str]) -> DocumentRun:
        workbench_root = Path(root).absolute()
        cls._ensure_real_directory(workbench_root, "workbench root")
        lock_file = cls._acquire_lock(workbench_root)
        try:
            manifest = cls._read_manifest(workbench_root)
            instance = cls(workbench_root, lock_file, manifest)
            instance._validate_persisted_data()
            return instance
        except BaseException:
            cls._release_lock(lock_file)
            raise

    @property
    def snapshot_root(self) -> Path:
        self._ensure_open()
        return self._root / _INPUTS_NAME

    @staticmethod
    def _remove_uncommitted_inputs(
        root: Path, manifest_temporaries: tuple[Path, ...]
    ) -> None:
        inputs_root = root / _INPUTS_NAME
        if inputs_root.exists() or inputs_root.is_symlink():
            if not stat.S_ISDIR(inputs_root.lstat().st_mode):
                raise WorkbenchCorruptError(
                    f"uncommitted input path must be a real directory: {inputs_root}"
                )
        for path in manifest_temporaries:
            if not stat.S_ISREG(path.lstat().st_mode):
                raise WorkbenchCorruptError(
                    f"temporary manifest must be a regular file: {path}"
                )
        # No manifest means the inputs and temporary manifests were never committed.
        for path in manifest_temporaries:
            path.unlink()
        if inputs_root.exists():
            shutil.rmtree(inputs_root)

    def register_units(self, unit_ids: Iterable[str]) -> None:
        self._ensure_open()
        self._verify_inputs()
        normalized = self._normalize_unit_ids(unit_ids)

        registered = self._manifest["unit_ids"]
        if registered is not None:
            if tuple(registered) != normalized:
                raise WorkbenchStateError(
                    "registered unit IDs do not match the existing workbench plan"
                )
            return

        if self._manifest["phase"] != WorkbenchPhase.SNAPSHOT_READY.value:
            raise WorkbenchStateError(
                "unit plan can only be registered after the input snapshot"
            )
        updated = dict(self._manifest)
        updated["unit_ids"] = list(normalized)
        updated["phase"] = (
            WorkbenchPhase.PROCESSING.value
            if normalized
            else WorkbenchPhase.COMPLETED.value
        )
        self._commit_manifest(updated)

    def pending_units(self) -> tuple[str, ...]:
        self._ensure_open()
        self._verify_inputs()
        self._verify_completed_outputs()
        unit_ids = self._manifest["unit_ids"]
        if unit_ids is None:
            raise WorkbenchStateError("register the unit plan before querying pending units")
        completed = set(self._manifest["completions"])
        return tuple(unit_id for unit_id in unit_ids if unit_id not in completed)

    def status(self) -> WorkbenchStatus:
        self._ensure_open()
        self._validate_persisted_data()
        unit_ids = self._manifest["unit_ids"]
        pending = (
            ()
            if unit_ids is None
            else tuple(
                unit_id
                for unit_id in unit_ids
                if unit_id not in self._manifest["completions"]
            )
        )
        completions = tuple(
            UnitCompletion(
                unit_id=unit_id,
                size_bytes=record["size_bytes"],
                sha256=record["sha256"],
            )
            for unit_id, record in sorted(self._manifest["completions"].items())
        )
        return WorkbenchStatus(
            phase=WorkbenchPhase(self._manifest["phase"]),
            unit_ids=tuple(unit_ids or ()),
            pending_unit_ids=pending,
            completions=completions,
        )

    def prepare_unit_output(self, unit_id: str) -> Path:
        self._ensure_open()
        unit_id = _validate_unit_id(unit_id)
        self._require_pending_unit(unit_id)
        output_path = self._output_path_for(unit_id)
        self._create_output_parent(output_path.parent)
        if output_path.is_symlink():
            raise WorkbenchCorruptError(
                f"unit output must not be a symlink: {unit_id!r}"
            )
        if output_path.exists():
            if not output_path.is_file():
                raise WorkbenchCorruptError(
                    f"unit output is not a regular file: {unit_id!r}"
                )
            output_path.unlink()
        return output_path

    def mark_unit_completed(self, unit_id: str) -> UnitCompletion:
        self._ensure_open()
        unit_id = _validate_unit_id(unit_id)
        self._verify_inputs()
        self._require_registered_unit(unit_id)
        output_path = self._output_path_for(unit_id)
        if not output_path.exists() and not output_path.is_symlink():
            raise WorkbenchStateError(
                f"unit output does not exist or is not a regular file: {unit_id!r}"
            )
        self._ensure_safe_output_path(output_path)
        if not output_path.is_file():
            raise WorkbenchStateError(
                f"unit output does not exist or is not a regular file: {unit_id!r}"
            )

        size_bytes, digest = self._digest_file(output_path, sync=True)
        self._fsync_output_directories(output_path.parent)
        existing = self._manifest["completions"].get(unit_id)
        if existing is not None:
            if existing != {"size_bytes": size_bytes, "sha256": digest}:
                raise WorkbenchCorruptError(
                    f"completed unit output has changed: {unit_id!r}"
                )
            return UnitCompletion(unit_id, size_bytes, digest)

        updated = dict(self._manifest)
        completions = dict(self._manifest["completions"])
        completions[unit_id] = {"size_bytes": size_bytes, "sha256": digest}
        updated["completions"] = completions
        if set(completions) == set(updated["unit_ids"]):
            updated["phase"] = WorkbenchPhase.COMPLETED.value
        else:
            updated["phase"] = WorkbenchPhase.PROCESSING.value
        self._commit_manifest(updated)
        return UnitCompletion(unit_id, size_bytes, digest)

    def output_path(self, unit_id: str) -> Path:
        self._ensure_open()
        unit_id = _validate_unit_id(unit_id)
        self._verify_inputs()
        record = self._manifest["completions"].get(unit_id)
        if record is None:
            raise WorkbenchStateError(
                f"unit has no completed output: {unit_id!r}"
            )
        output_path = self._output_path_for(unit_id)
        self._ensure_safe_output_path(output_path)
        if not output_path.is_file():
            raise WorkbenchCorruptError(
                f"completed unit output is missing: {unit_id!r}"
            )
        size_bytes, digest = self._digest_file(output_path)
        if (size_bytes, digest) != (record["size_bytes"], record["sha256"]):
            raise WorkbenchCorruptError(
                f"completed unit output failed integrity check: {unit_id!r}"
            )
        return output_path

    def close(self) -> None:
        if self._closed:
            return
        self._release_lock(self._lock_file)
        self._closed = True

    def __enter__(self) -> DocumentRun:
        self._ensure_open()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def _commit_manifest(self, manifest: dict[str, Any]) -> None:
        self._write_manifest(self._root, manifest)
        self._manifest = manifest

    def _require_pending_unit(self, unit_id: str) -> None:
        self._require_registered_unit(unit_id)
        if unit_id in self._manifest["completions"]:
            raise WorkbenchStateError(f"unit is already completed: {unit_id!r}")

    def _require_registered_unit(self, unit_id: str) -> None:
        unit_ids = self._manifest["unit_ids"]
        if unit_ids is None:
            raise WorkbenchStateError("register the unit plan before processing units")
        if unit_id not in unit_ids:
            raise WorkbenchStateError(f"unknown unit ID: {unit_id!r}")

    def _output_path_for(self, unit_id: str) -> Path:
        return self._root / _OUTPUTS_NAME / PurePosixPath(unit_id)

    def _create_output_parent(self, parent: Path) -> None:
        outputs_root = self._root / _OUTPUTS_NAME
        if outputs_root.is_symlink():
            raise WorkbenchCorruptError("output root must not be a symlink")
        if not outputs_root.exists():
            outputs_root.mkdir()
        self._ensure_real_directory(outputs_root, "output root")
        relative = parent.relative_to(outputs_root)
        current = outputs_root
        for part in relative.parts:
            current = current / part
            if current.exists() or current.is_symlink():
                self._ensure_real_directory(current, "unit output directory")
            else:
                current.mkdir()

    def _ensure_safe_output_path(self, output_path: Path) -> None:
        self._ensure_real_directory(self._root / _OUTPUTS_NAME, "output root")
        current = output_path.parent
        while current != self._root / _OUTPUTS_NAME:
            self._ensure_real_directory(current, "unit output directory")
            current = current.parent
        if output_path.is_symlink():
            raise WorkbenchCorruptError("unit output must not be a symlink")

    def _validate_persisted_data(self) -> None:
        self._ensure_manifest_shape(self._manifest)
        self._verify_inputs()
        self._verify_completed_outputs()

    def _verify_completed_outputs(self) -> None:
        for unit_id in self._manifest["completions"]:
            output_path = self._output_path_for(unit_id)
            self._ensure_safe_output_path(output_path)
            if not output_path.is_file():
                raise WorkbenchCorruptError(
                    f"completed unit output is missing: {unit_id!r}"
                )
            record = self._manifest["completions"][unit_id]
            size_bytes, digest = self._digest_file(output_path)
            if (size_bytes, digest) != (record["size_bytes"], record["sha256"]):
                raise WorkbenchCorruptError(
                    f"completed unit output failed integrity check: {unit_id!r}"
                )

    def _verify_inputs(self) -> None:
        inputs_root = self._root / _INPUTS_NAME
        self._ensure_real_directory(inputs_root, "input snapshot")
        actual = self._inventory_tree(inputs_root)
        if actual != self._manifest["inputs"]:
            raise WorkbenchCorruptError("input snapshot failed integrity check")

    def _ensure_open(self) -> None:
        if self._closed:
            raise WorkbenchStateError("workbench is closed")

    @staticmethod
    def _normalize_unit_ids(unit_ids: Iterable[str]) -> tuple[str, ...]:
        if isinstance(unit_ids, (str, bytes)):
            raise InvalidUnitIdError("unit IDs must be an iterable of path strings")
        validated: list[str] = []
        seen: set[str] = set()
        for unit_id in unit_ids:
            normalized = _validate_unit_id(unit_id)
            if normalized in seen:
                raise InvalidUnitIdError(f"duplicate unit ID: {normalized!r}")
            seen.add(normalized)
            validated.append(normalized)

        ordered = tuple(sorted(validated))
        ids_by_casefold = {unit_id.casefold(): unit_id for unit_id in ordered}
        if len(ids_by_casefold) != len(ordered):
            for unit_id in ordered:
                if ids_by_casefold[unit_id.casefold()] != unit_id:
                    raise InvalidUnitIdError(
                        "unit IDs collide on case-insensitive filesystems: "
                        f"{unit_id!r}, {ids_by_casefold[unit_id.casefold()]!r}"
                    )
        exact_ids = set(ordered)
        for unit_id in ordered:
            parts = unit_id.split("/")
            for index in range(1, len(parts)):
                parent = "/".join(parts[:index])
                if parent in exact_ids:
                    raise InvalidUnitIdError(
                        f"unit IDs cannot contain file/directory collisions: "
                        f"{parent!r}, {unit_id!r}"
                    )
                folded_parent = parent.casefold()
                if folded_parent in ids_by_casefold:
                    conflicting_id = ids_by_casefold[folded_parent]
                    if conflicting_id != parent:
                        raise InvalidUnitIdError(
                            "unit IDs collide on case-insensitive filesystems: "
                            f"{conflicting_id!r}, {unit_id!r}"
                        )
        return ordered

    @staticmethod
    def _ensure_manifest_shape(manifest: Any) -> None:
        if not isinstance(manifest, dict):
            raise WorkbenchCorruptError("workbench manifest must be a JSON object")
        version = manifest.get("schema_version")
        if not _is_nonnegative_int(version) or version != _SCHEMA_VERSION:
            raise UnsupportedWorkbenchVersionError(
                f"unsupported workbench schema version: {version!r}"
            )
        expected_keys = {
            "schema_version",
            "phase",
            "inputs",
            "unit_ids",
            "completions",
        }
        if set(manifest) != expected_keys:
            raise WorkbenchCorruptError("workbench manifest has invalid fields")
        try:
            phase = WorkbenchPhase(manifest["phase"])
        except (ValueError, TypeError) as error:
            raise WorkbenchCorruptError("workbench manifest has invalid phase") from error

        inputs = manifest["inputs"]
        if not isinstance(inputs, dict):
            raise WorkbenchCorruptError("workbench input inventory must be an object")
        for relative, record in inputs.items():
            try:
                _validate_relative_path(relative)
            except InvalidUnitIdError as error:
                raise WorkbenchCorruptError(
                    "workbench input inventory has an invalid path"
                ) from error
            if (
                not isinstance(record, dict)
                or set(record) != {"size_bytes", "sha256"}
                or not _is_nonnegative_int(record["size_bytes"])
                or not _is_sha256(record["sha256"])
            ):
                raise WorkbenchCorruptError("workbench input inventory is invalid")

        unit_ids = manifest["unit_ids"]
        if unit_ids is not None:
            if not isinstance(unit_ids, list) or any(
                not isinstance(unit_id, str) for unit_id in unit_ids
            ):
                raise WorkbenchCorruptError("workbench unit plan is invalid")
            try:
                if DocumentRun._normalize_unit_ids(unit_ids) != tuple(unit_ids):
                    raise WorkbenchCorruptError(
                        "workbench unit plan is not in canonical order"
                    )
            except InvalidUnitIdError as error:
                raise WorkbenchCorruptError("workbench unit plan is invalid") from error

        completions = manifest["completions"]
        if not isinstance(completions, dict):
            raise WorkbenchCorruptError("workbench completions must be an object")
        for unit_id, record in completions.items():
            try:
                _validate_unit_id(unit_id)
            except InvalidUnitIdError as error:
                raise WorkbenchCorruptError("workbench completion ID is invalid") from error
            if (
                not isinstance(record, dict)
                or set(record) != {"size_bytes", "sha256"}
                or not _is_nonnegative_int(record["size_bytes"])
                or not _is_sha256(record["sha256"])
            ):
                raise WorkbenchCorruptError("workbench completion record is invalid")

        if unit_ids is None:
            if phase is not WorkbenchPhase.SNAPSHOT_READY or completions:
                raise WorkbenchCorruptError("workbench phase is inconsistent")
        else:
            unit_set = set(unit_ids)
            completion_set = set(completions)
            if not completion_set <= unit_set:
                raise WorkbenchCorruptError(
                    "workbench contains completions for unregistered units"
                )
            if phase is WorkbenchPhase.SNAPSHOT_READY:
                raise WorkbenchCorruptError("registered plan cannot be snapshot_ready")
            if phase is WorkbenchPhase.PROCESSING:
                if not unit_set or completion_set == unit_set:
                    raise WorkbenchCorruptError("workbench phase is inconsistent")
            elif completion_set != unit_set:
                raise WorkbenchCorruptError("completed workbench has pending units")

    @classmethod
    def _read_manifest(cls, root: Path) -> dict[str, Any]:
        path = root / _MANIFEST_NAME
        if path.is_symlink() or not path.is_file():
            raise WorkbenchCorruptError("workbench manifest is missing or not a file")
        try:
            stream = path.open("r", encoding="utf-8")
        except FileNotFoundError as error:
            raise WorkbenchCorruptError("workbench manifest is missing") from error
        try:
            with stream:
                manifest = json.load(stream)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise WorkbenchCorruptError("workbench manifest cannot be read") from error
        cls._ensure_manifest_shape(manifest)
        return manifest

    @classmethod
    def _write_manifest(cls, root: Path, manifest: dict[str, Any]) -> None:
        cls._ensure_manifest_shape(manifest)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=_MANIFEST_TEMP_PREFIX, suffix=_MANIFEST_TEMP_SUFFIX, dir=root
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as stream:
                json.dump(manifest, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, root / _MANIFEST_NAME)
            cls._fsync_directory(root)
        except BaseException:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise

    @classmethod
    def _inventory_tree(
        cls,
        root: Path,
        *,
        sync: bool = False,
    ) -> dict[str, dict[str, Any]]:
        cls._ensure_real_directory(root, "snapshot directory")
        inventory: dict[str, dict[str, Any]] = {}
        for current_root, directory_names, file_names in os.walk(
            root, topdown=True, followlinks=False
        ):
            current_path = Path(current_root)
            for directory_name in directory_names:
                directory = current_path / directory_name
                if directory.is_symlink() or not directory.is_dir():
                    raise WorkbenchCorruptError(
                        f"snapshot contains an unsafe directory: {directory}"
                    )
            for file_name in file_names:
                path = current_path / file_name
                mode = path.lstat().st_mode
                if not stat.S_ISREG(mode):
                    raise WorkbenchCorruptError(
                        f"snapshot contains a non-regular file: {path}"
                    )
                size_bytes, digest = cls._digest_file(path, sync=sync)
                relative = path.relative_to(root).as_posix()
                inventory[relative] = {
                    "size_bytes": size_bytes,
                    "sha256": digest,
                }
        return dict(sorted(inventory.items()))

    @staticmethod
    def _digest_file(path: Path, *, sync: bool = False) -> tuple[int, str]:
        digest = hashlib.sha256()
        size_bytes = 0
        # Windows requires a writable handle when flushing file data.
        mode = "r+b" if sync and os.name == "nt" else "rb"
        with path.open(mode) as stream:
            if sync:
                os.fsync(stream.fileno())
            while chunk := stream.read(1024 * 1024):
                size_bytes += len(chunk)
                digest.update(chunk)
        return size_bytes, digest.hexdigest()

    @staticmethod
    def _ensure_real_directory(path: Path, label: str) -> None:
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError as error:
            raise WorkbenchCorruptError(f"{label} is unavailable: {path}") from error
        if not stat.S_ISDIR(mode):
            raise WorkbenchCorruptError(f"{label} must be a real directory: {path}")

    @staticmethod
    def _acquire_lock(root: Path) -> BinaryIO:
        lock_path = root / _LOCK_NAME
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(lock_path, flags, 0o600)
        except OSError as error:
            if error.errno == errno.ELOOP:
                raise WorkbenchCorruptError("workbench lock must not be a symlink") from error
            raise
        lock_file = os.fdopen(descriptor, "r+b", buffering=0)
        try:
            _lock_file(lock_file)
        except OSError as error:
            lock_file.close()
            if isinstance(error, BlockingIOError) or error.errno in {
                errno.EACCES,
                errno.EAGAIN,
                errno.EDEADLK,
            }:
                raise WorkbenchBusyError(
                    f"workbench is already open: {root}"
                ) from error
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

    @classmethod
    def _fsync_tree_directories(cls, root: Path) -> None:
        directories = [
            Path(current_root)
            for current_root, _, _ in os.walk(root, topdown=False, followlinks=False)
        ]
        for directory in directories:
            cls._fsync_directory(directory)

    def _fsync_output_directories(self, parent: Path) -> None:
        outputs_root = self._root / _OUTPUTS_NAME
        current = parent
        while True:
            self._fsync_directory(current)
            if current == outputs_root:
                break
            current = current.parent


def _validate_unit_id(unit_id: str) -> str:
    if not isinstance(unit_id, str):
        raise InvalidUnitIdError("unit ID must be a string")
    _validate_relative_path(unit_id)
    return unit_id


def _validate_relative_path(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidUnitIdError("path ID must be a non-empty string")
    if (
        value.startswith("/")
        or "\\" in value
        or "\x00" in value
        or _DRIVE_PREFIX.match(value)
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise InvalidUnitIdError(
            f"path ID must be a canonical relative POSIX path: {value!r}"
        )
    if PurePosixPath(value).as_posix() != value:
        raise InvalidUnitIdError(
            f"path ID must be a canonical relative POSIX path: {value!r}"
        )
    return value


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _HASH_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


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
