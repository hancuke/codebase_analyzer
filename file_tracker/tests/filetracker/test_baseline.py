"""Tests for baseline persistence, integrity, and linked undo snapshots."""

from __future__ import annotations

import os

import pytest

from filetracker.baseline import BaselineError, BaselineManager
from filetracker.models import ContentAvailability


def _manifest(content: str, revision: str) -> dict:
    return {
        "version": 1,
        "revision": revision,
        "files": {
            "a.py": {
                "path": "a.py",
                "exists": True,
                "size": len(content),
                "mtime": 1.0,
                "sha256": "x",
                "content": content,
            }
        },
    }


def test_atomic_manifest_update(tmp_path, monkeypatch):
    baseline = BaselineManager(str(tmp_path))
    baseline.save(_manifest("old", "old"))

    def fail_replace(source, destination):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr(os, "replace", fail_replace)

    with pytest.raises(OSError):
        baseline.save(_manifest("new", "new"))

    assert baseline.load()["files"]["a.py"]["content"] == "old"


def test_missing_manifest_is_an_empty_initial_baseline(tmp_path):
    baseline = BaselineManager(str(tmp_path))

    assert baseline.load() == {"version": 1, "revision": "initial", "files": {}}


def test_corrupt_manifest_raises_instead_of_resetting_baseline(tmp_path):
    baseline = BaselineManager(str(tmp_path))
    (tmp_path / "manifest.json").write_text("{not json")

    with pytest.raises(BaselineError, match="Could not read baseline manifest"):
        baseline.load()


def test_advance_persists_undo_snapshot_before_manifest(tmp_path, monkeypatch):
    baseline = BaselineManager(str(tmp_path))
    current = _manifest("v1", "v1")
    baseline.save(current)

    def fail_save(manifest):
        raise OSError("manifest write failed")

    monkeypatch.setattr(baseline, "save", fail_save)

    with pytest.raises(OSError):
        baseline.advance(current, _manifest("v2", "v2"))

    snapshot_files = list((tmp_path / "snapshots").glob("*.json"))
    assert len(snapshot_files) == 1
    assert baseline.load()["revision"] == "v1"


def test_linked_snapshots_restore_multiple_revisions(tmp_path):
    baseline = BaselineManager(str(tmp_path))
    initial = _manifest("v1", "v1")
    baseline.save(initial)
    baseline.advance(initial, _manifest("v2", "v2"))
    second = baseline.load()
    baseline.advance(second, _manifest("v3", "v3"))

    restored = baseline.undo()
    assert restored is not None
    assert restored["files"]["a.py"]["content"] == "v2"

    restored = baseline.undo()
    assert restored is not None
    assert restored["files"]["a.py"]["content"] == "v1"
    assert baseline.undo() is None


def test_utf16_object_is_read_as_text(tmp_path):
    baseline = BaselineManager(str(tmp_path))
    data = "Begin Form\nEnd\n".encode("utf-16")
    key = baseline.store_object(data)
    manifest = {"version": 1, "revision": "r1", "files": {"form.frm": {"sha256": key}}}

    snapshot = baseline.get_content_snapshot(manifest, "form.frm")

    assert snapshot.availability is ContentAvailability.TEXT
    assert snapshot.text == "Begin Form\nEnd\n"
