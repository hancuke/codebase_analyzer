"""FileTracker: the main API for physical file-level change tracking."""

from __future__ import annotations

import os
import hashlib
from datetime import datetime, timezone
from pathlib import Path

from filetracker.baseline import BaselineManager
from filetracker.diff import is_binary
from filetracker.models import (
    ChangeSet,
    ChangeStatus,
    ContentAvailability,
    ContentSnapshot,
    FileChange,
    FileState,
)
from filetracker.scanner import FileScanner

DEFAULT_BASELINE_SUBDIR = os.path.join(".filetracker", "baseline")


class RevisionConflictError(RuntimeError):
    """Raised when a scan is no longer valid for a requested commit."""


def _read_content_snapshot(abs_path: Path) -> ContentSnapshot:
    """Read a file once and classify its content for a scan snapshot."""
    try:
        with open(abs_path, "rb") as fh:
            data = fh.read()
    except OSError:
        return ContentSnapshot(ContentAvailability.UNREADABLE)
    if is_binary(data):
        return ContentSnapshot(ContentAvailability.BINARY)
    try:
        return ContentSnapshot(ContentAvailability.TEXT, data.decode("utf-8"))
    except UnicodeDecodeError:
        return ContentSnapshot(ContentAvailability.UNDECODABLE)


def _read_bytes(abs_path: Path) -> bytes | None:
    """Read a file's raw bytes; return None if it cannot be read."""
    try:
        with open(abs_path, "rb") as fh:
            return fh.read()
    except OSError:
        return None


def _entry_to_state(entry: dict) -> FileState:
    return FileState(
        path=Path(entry.get("path", "")),
        exists=entry.get("exists", True),
        size=entry.get("size"),
        mtime=entry.get("mtime"),
        sha256=entry.get("sha256"),
    )


def _path_key(path: Path | str) -> str:
    return path.as_posix() if isinstance(path, Path) else path


class FileTracker:
    """Track physical file changes relative to a committed baseline.

    Typical workflow::

        tracker = FileTracker(root="./src")
        changes = tracker.scan()          # read-only
        if changes.has_changes:
            ... process ...
            tracker.commit("advanced")    # advance baseline
    """

    def __init__(
        self,
        root: str,
        exclude_patterns: list[str] | None = None,
        baseline_dir: str | None = None,
    ):
        self.root = os.path.abspath(root)
        self.exclude_patterns = tuple(exclude_patterns or [])
        if baseline_dir is None:
            baseline_dir = os.path.join(self.root, DEFAULT_BASELINE_SUBDIR)
        self.baseline = BaselineManager(baseline_dir)
        self.scanner = FileScanner(
            self.root,
            exclude_patterns=self.exclude_patterns,
            baseline_dir=self.baseline.baseline_dir,
        )
        self._baseline_cache: dict | None = None

    # ---- public API ---------------------------------------------------

    def scan(self) -> ChangeSet:
        """Compute file-level changes vs. the current baseline (no mutation)."""
        old_manifest = self.baseline.load()
        self._baseline_cache = old_manifest

        baseline_states: dict[Path, FileState] = {}
        for path, entry in old_manifest["files"].items():
            if entry.get("exists", True):
                baseline_states[Path(path)] = _entry_to_state(entry)

        working_states = self.scanner.scan()

        changes: list[FileChange] = []

        for path in sorted(set(baseline_states) | set(working_states)):
            baseline_state = baseline_states.get(path)
            working_state = working_states.get(path)
            if baseline_state is None and working_state is not None:
                changes.append(
                    FileChange(
                        path=path,
                        status=ChangeStatus.ADDED,
                        baseline_state=None,
                        working_state=working_state,
                        baseline_content=ContentSnapshot(ContentAvailability.ABSENT),
                        working_content=self.read_working_snapshot(path),
                    )
                )
            elif baseline_state is not None and working_state is None:
                changes.append(
                    FileChange(
                        path=path,
                        status=ChangeStatus.DELETED,
                        baseline_state=baseline_state,
                        working_state=None,
                        baseline_content=self.read_baseline_snapshot(path),
                        working_content=ContentSnapshot(ContentAvailability.ABSENT),
                    )
                )
            elif baseline_state is not None and working_state is not None:
                if baseline_state.sha256 != working_state.sha256:
                    changes.append(
                        FileChange(
                            path=path,
                            status=ChangeStatus.MODIFIED,
                            baseline_state=baseline_state,
                            working_state=working_state,
                            baseline_content=self.read_baseline_snapshot(path),
                            working_content=self.read_working_snapshot(path),
                        )
                    )

        return ChangeSet(
            files=tuple(changes),
            baseline_revision=old_manifest["revision"],
            working_revision=self._working_revision(working_states),
        )

    def commit(
        self,
        message: str = "",
        expected_revision: str | None = None,
        expected_baseline_revision: str | None = None,
    ) -> None:
        """Advance the baseline to the current working directory state.

        The manifest write is atomic: :meth:`BaselineManager.save` raises
        *before* the final ``os.replace``, so a failure leaves the on-disk
        baseline completely untouched (transactional integrity).
        """
        current_manifest = self.baseline.load()
        if (
            expected_baseline_revision is not None
            and expected_baseline_revision != current_manifest["revision"]
        ):
            raise RevisionConflictError(
                "Baseline changed since scan; scan again before committing."
            )
        new_manifest, working_revision = self._build_manifest_from_working(message)
        if expected_revision is not None and expected_revision != working_revision:
            raise RevisionConflictError(
                "Working tree changed since scan; scan again before committing."
            )
        self._baseline_cache = self.baseline.advance(current_manifest, new_manifest)

    def undo(self) -> bool:
        """Roll the baseline back by one commit. Returns True if a rollback
        happened, False if there was nothing to undo.

        This only mutates the baseline; working-directory files are untouched.
        """
        previous_manifest = self.baseline.undo()
        if previous_manifest is None:
            return False
        self._baseline_cache = previous_manifest
        return True

    # ---- content accessors (used while building scan snapshots) --------

    def read_baseline_snapshot(self, path: Path | str) -> ContentSnapshot:
        manifest = (
            self._baseline_cache
            if self._baseline_cache is not None
            else self.baseline.load()
        )
        return self.baseline.get_content_snapshot(manifest, path)

    def read_working_snapshot(self, path: Path | str) -> ContentSnapshot:
        rel_path = path if isinstance(path, Path) else Path(path)
        abs_path = Path(self.root, rel_path)
        return _read_content_snapshot(abs_path)

    def resolve_path(self, relative_path: Path | str) -> Path:
        """Resolve a tracked relative path against this tracker's root."""
        return Path(self.root, relative_path)

    # ---- internals ----------------------------------------------------

    def _build_manifest_from_working(self, message: str = "") -> tuple[dict, str]:
        states = self.scanner.scan()
        files: dict[str, dict] = {}
        for path, st in states.items():
            abs_path = Path(self.root, path)
            data = _read_bytes(abs_path)
            if data is None or st.sha256 != hashlib.sha256(data).hexdigest():
                raise RevisionConflictError(
                    f"Working file changed while committing: {path}"
                )
            # Content is stored externally as a content-addressed object keyed
            # by sha256 (which the scanner already computed), so the manifest
            # only carries the reference, not the bytes.
            if st.sha256 is not None:
                self.baseline.store_object(data, key=st.sha256)
            path_key = _path_key(path)
            files[path_key] = {
                "path": path_key,
                "exists": True,
                "size": st.size,
                "mtime": st.mtime,
                "sha256": st.sha256,
            }
        return {
            "version": 1,
            "message": message,
            "committed_at": datetime.now(timezone.utc).isoformat(),
            "files": files,
        }, self._working_revision(states)

    @staticmethod
    def _working_revision(states: dict[Path, FileState]) -> str:
        digest = hashlib.sha256()
        for path, state in sorted(states.items()):
            digest.update(path.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update((state.sha256 or "").encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()
