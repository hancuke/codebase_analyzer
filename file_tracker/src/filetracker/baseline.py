"""Manifest persistence with atomic writes and undo snapshots.

The manifest is a JSON document mapping relative file paths to their
:class:`FileState`-equivalent entry. File *content* is **not** stored inline;
instead each entry references an immutable object in the ``objects/``
subdirectory, keyed by the file's sha256 (already computed by the scanner).
This keeps the manifest small, de-duplicates identical content across files
and commits, and lets undo snapshots stay tiny (they only store metadata).

Objects are written atomically (``*.tmp`` + fsync + ``os.replace``) and, being
content-addressed, are never overwritten once written.

Atomicity of the manifest is guaranteed by writing to a ``*.tmp`` file first,
flushing and fsync-ing it, then performing a single atomic ``os.replace`` onto
the final path. A crash before the replace leaves the previous manifest
untouched.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from pathlib import Path

from filetracker.diff import is_binary
from filetracker.models import ContentAvailability, ContentSnapshot

MANIFEST_FILENAME = "manifest.json"
OBJECTS_DIRNAME = "objects"
SNAPSHOT_DIRNAME = "snapshots"


class BaselineError(RuntimeError):
    """Raised when persisted baseline state cannot be read safely."""


class BaselineManager:
    def __init__(self, baseline_dir: str):
        self.baseline_dir = os.path.abspath(baseline_dir)
        self.manifest_path = os.path.join(self.baseline_dir, MANIFEST_FILENAME)
        self.objects_dir = os.path.join(self.baseline_dir, OBJECTS_DIRNAME)
        self.snapshots_dir = os.path.join(self.baseline_dir, SNAPSHOT_DIRNAME)
        os.makedirs(self.baseline_dir, exist_ok=True)
        os.makedirs(self.objects_dir, exist_ok=True)
        os.makedirs(self.snapshots_dir, exist_ok=True)

    # ---- manifest I/O -------------------------------------------------

    def load(self) -> dict:
        """Return the current manifest, or an empty manifest if none exists."""
        if not os.path.exists(self.manifest_path):
            return {"version": 1, "revision": "initial", "files": {}}
        try:
            with open(self.manifest_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as error:
            raise BaselineError(
                f"Could not read baseline manifest: {self.manifest_path}"
            ) from error
        if not isinstance(data, dict) or not isinstance(data.get("files", {}), dict):
            raise BaselineError(
                f"Baseline manifest has an invalid structure: {self.manifest_path}"
            )
        data.setdefault("version", 1)
        data.setdefault("files", {})
        data.setdefault("revision", self._legacy_revision(data))
        return data

    def save(self, manifest: dict) -> None:
        """Atomically write *manifest* to disk.

        Raises on failure *before* the final replace, so the on-disk manifest
        is never left in a half-written state.
        """
        os.makedirs(self.baseline_dir, exist_ok=True)
        # Write to a temp file in the same directory (same filesystem -> atomic move).
        fd, tmp_name = tempfile.mkstemp(
            dir=self.baseline_dir, suffix=".tmp", prefix=".manifest-"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.manifest_path)
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.remove(tmp_name)
                except OSError:
                    pass

    # ---- content access (external object store) -----------------------

    def store_object(self, data: bytes, key: str | None = None) -> str:
        """Persist *data* in the content-addressed object store.

        The object is keyed by *key* (default: sha256 of *data*), which is
        also returned so callers can reference it from the manifest. Writes
        are atomic and idempotent: an object with the same key is never
        rewritten, so identical content is stored exactly once.
        """
        if key is None:
            key = hashlib.sha256(data).hexdigest()
        obj_path = os.path.join(self.objects_dir, key)
        if os.path.exists(obj_path):
            return key
        fd, tmp_name = tempfile.mkstemp(
            dir=self.objects_dir, prefix=".obj-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, obj_path)
        finally:
            if os.path.exists(tmp_name):
                try:
                    os.remove(tmp_name)
                except OSError:
                    pass
        return key

    def read_object(self, key: str) -> bytes | None:
        """Return the raw bytes for *key*, or None if the object is missing."""
        obj_path = os.path.join(self.objects_dir, key)
        try:
            with open(obj_path, "rb") as fh:
                return fh.read()
        except OSError:
            return None

    def get_content_snapshot(
        self, manifest: dict, path: str | Path
    ) -> ContentSnapshot:
        """Return a classified baseline content snapshot for *path*."""
        key_path = path.as_posix() if isinstance(path, Path) else path
        entry = manifest["files"].get(key_path)
        if entry is None:
            return ContentSnapshot(ContentAvailability.ABSENT)
        key = entry.get("sha256")
        if key is not None:
            data = self.read_object(key)
            if data is None:
                legacy_content = entry.get("content")
                if isinstance(legacy_content, str):
                    return ContentSnapshot(ContentAvailability.TEXT, legacy_content)
                return ContentSnapshot(ContentAvailability.UNREADABLE)
            if is_binary(data):
                return ContentSnapshot(ContentAvailability.BINARY)
            try:
                return ContentSnapshot(ContentAvailability.TEXT, data.decode("utf-8"))
            except UnicodeDecodeError:
                return ContentSnapshot(ContentAvailability.UNDECODABLE)
        # Backward-compat: legacy manifests stored text inline.
        content = entry.get("content")
        if isinstance(content, str):
            return ContentSnapshot(ContentAvailability.TEXT, content)
        return ContentSnapshot(ContentAvailability.UNREADABLE)

    # ---- undo snapshots ------------------------------------------------

    def advance(self, current_manifest: dict, next_manifest: dict) -> dict:
        """Atomically advance the manifest after safely persisting its predecessor."""
        snapshot_name = f"{uuid.uuid4().hex}.json"
        self._save_snapshot(snapshot_name, current_manifest)
        committed_manifest = {
            **next_manifest,
            "revision": uuid.uuid4().hex,
            "undo_snapshot": snapshot_name,
        }
        self.save(committed_manifest)
        return committed_manifest

    def undo(self) -> dict | None:
        """Restore the manifest linked from the current revision, if present."""
        current_manifest = self.load()
        snapshot_name = current_manifest.get("undo_snapshot")
        if not isinstance(snapshot_name, str):
            return None
        previous_manifest = self._load_snapshot(snapshot_name)
        self.save(previous_manifest)
        try:
            os.remove(os.path.join(self.snapshots_dir, snapshot_name))
        except OSError:
            pass
        return previous_manifest

    def _save_snapshot(self, snapshot_name: str, manifest: dict) -> None:
        snapshot_path = os.path.join(self.snapshots_dir, snapshot_name)
        fd, temporary_path = tempfile.mkstemp(
            dir=self.snapshots_dir, suffix=".tmp", prefix=".snapshot-"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, ensure_ascii=False, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(temporary_path, snapshot_path)
        finally:
            if os.path.exists(temporary_path):
                os.remove(temporary_path)

    def _load_snapshot(self, snapshot_name: str) -> dict:
        snapshot_path = os.path.join(self.snapshots_dir, snapshot_name)
        try:
            with open(snapshot_path, "r", encoding="utf-8") as fh:
                snapshot = json.load(fh)
        except (json.JSONDecodeError, OSError) as error:
            raise BaselineError(
                f"Could not read baseline snapshot: {snapshot_path}"
            ) from error
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("files"), dict):
            raise BaselineError(f"Baseline snapshot has an invalid structure: {snapshot_path}")
        return snapshot

    @staticmethod
    def _legacy_revision(manifest: dict) -> str:
        payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
