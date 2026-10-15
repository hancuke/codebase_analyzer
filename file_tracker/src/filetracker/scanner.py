"""Recursive file scanner producing :class:`FileState` snapshots."""

from __future__ import annotations

import fnmatch
import hashlib
import os
from pathlib import Path

from filetracker.models import FileState


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class FileScanner:
    """Walk *root* and collect a :class:`FileState` for every tracked file.

    Paths are stored relative to *root* using POSIX-style separators.
    """

    def __init__(
        self,
        root: str,
        exclude_patterns: list[str] | tuple[str, ...] | None = None,
        baseline_dir: str | None = None,
        include_patterns: list[str] | tuple[str, ...] | None = None,
    ):
        self.root = os.path.abspath(root)
        self.include_patterns = tuple(include_patterns or [])
        self.exclude_patterns = tuple(exclude_patterns or [])
        self.baseline_dir = os.path.abspath(baseline_dir) if baseline_dir else None

    @staticmethod
    def _matches_any(relative_path: Path, patterns: tuple[str, ...]) -> bool:
        path = relative_path.as_posix()
        return any(
            fnmatch.fnmatch(path, pattern)
            or (
                pattern.startswith("**/")
                and fnmatch.fnmatch(path, pattern.removeprefix("**/"))
            )
            for pattern in patterns
        )

    def _is_excluded(self, relative_path: Path) -> bool:
        return self._matches_any(relative_path, self.exclude_patterns)

    def matches(self, relative_path: Path) -> bool:
        """Return whether a relative path is inside this scanner's file scope."""
        return (
            (
                not self.include_patterns
                or self._matches_any(relative_path, self.include_patterns)
            )
            and not self._is_excluded(relative_path)
        )

    def _is_baseline_path(self, abs_path: str) -> bool:
        if not self.baseline_dir:
            return False
        return abs_path == self.baseline_dir or abs_path.startswith(
            self.baseline_dir + os.sep
        )

    def scan(self) -> dict[Path, FileState]:
        states: dict[Path, FileState] = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            # Prune excluded directories in-place so os.walk skips them.
            kept = []
            for d in dirnames:
                abs_d = os.path.join(dirpath, d)
                relative_dir = Path(
                    os.path.relpath(abs_d, self.root).replace(os.sep, "/")
                )
                if self._is_baseline_path(abs_d) or self._is_excluded(relative_dir):
                    continue
                kept.append(d)
            dirnames[:] = kept

            for fname in filenames:
                abs_path = os.path.join(dirpath, fname)
                if self._is_baseline_path(abs_path):
                    continue
                rel = Path(os.path.relpath(abs_path, self.root).replace(os.sep, "/"))
                if not self.matches(rel):
                    continue
                states[rel] = self._state_for(rel, abs_path)
        return states

    def _state_for(self, rel_path: Path, abs_path: str) -> FileState:
        try:
            st = os.stat(abs_path)
        except OSError:
            return FileState(
                path=rel_path, exists=False, size=None, mtime=None, sha256=None
            )
        return FileState(
            path=rel_path,
            exists=True,
            size=st.st_size,
            mtime=st.st_mtime,
            sha256=_sha256_file(Path(abs_path)),
        )
