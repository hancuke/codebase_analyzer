"""Tests for FileTracker scanning, revisions, commits, and undo."""

from __future__ import annotations

from pathlib import Path

import pytest

from filetracker.models import ChangeStatus, ContentAvailability
from filetracker.tracker import FileTracker, RevisionConflictError


def _write(root, name, text):
    path = root / name
    path.write_bytes(text.encode("utf-8"))
    return path


def test_initial_scan_returns_added_text_snapshot(tmp_path):
    _write(tmp_path, "a.py", "x")
    tracker = FileTracker(root=str(tmp_path))

    changes = tracker.scan()
    change = changes.files[0]

    assert change.path == Path("a.py")
    assert change.status is ChangeStatus.ADDED
    assert change.baseline_content.availability is ContentAvailability.ABSENT
    assert change.working_content.text == "x"
    assert changes.baseline_revision == "initial"


def test_scan_after_commit_is_clean(tmp_path):
    _write(tmp_path, "a.py", "x")
    tracker = FileTracker(root=str(tmp_path))
    tracker.commit()

    assert tracker.scan().has_changes is False


def test_file_change_uses_immutable_content_snapshots(tmp_path):
    _write(tmp_path, "a.py", "v1")
    tracker = FileTracker(root=str(tmp_path))
    tracker.commit()
    _write(tmp_path, "a.py", "v2")

    changes = tracker.scan()
    _write(tmp_path, "a.py", "v3")
    change = changes.files[0]

    assert change.baseline_content.text == "v1"
    assert change.working_content.text == "v2"
    assert "-v1" in change.diff()
    assert "+v2" in change.diff()


def test_binary_content_is_explicitly_classified(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"\x00binary")
    tracker = FileTracker(root=str(tmp_path))

    change = tracker.scan().files[0]

    assert change.working_content.availability is ContentAvailability.BINARY
    assert change.working_content.text is None
    assert change.has_text_diff is False
    assert change.diff() == ""


def test_utf16_text_with_bom_is_not_classified_as_binary(tmp_path):
    path = tmp_path / "form.frm"
    path.write_bytes("Private Sub Save_Click()\nEnd Sub\n".encode("utf-16"))
    tracker = FileTracker(root=str(tmp_path))

    change = tracker.scan().files[0]

    assert change.working_content.availability is ContentAvailability.TEXT
    assert change.working_content.text == "Private Sub Save_Click()\nEnd Sub\n"


def test_commit_rejects_a_stale_scan_revision(tmp_path):
    _write(tmp_path, "a.py", "v1")
    tracker = FileTracker(root=str(tmp_path))
    tracker.commit()
    _write(tmp_path, "a.py", "v2")
    changes = tracker.scan()
    _write(tmp_path, "a.py", "v3")

    with pytest.raises(RevisionConflictError, match="Working tree changed"):
        tracker.commit(expected_revision=changes.working_revision)

    assert tracker.read_baseline_snapshot("a.py").text == "v1"


def test_commit_rejects_a_stale_baseline_revision(tmp_path):
    _write(tmp_path, "a.py", "v1")
    first_tracker = FileTracker(root=str(tmp_path))
    first_tracker.commit()
    changes = first_tracker.scan()
    second_tracker = FileTracker(root=str(tmp_path))
    second_tracker.commit(message="independent commit")

    with pytest.raises(RevisionConflictError, match="Baseline changed"):
        first_tracker.commit(
            expected_revision=changes.working_revision,
            expected_baseline_revision=changes.baseline_revision,
        )


def test_commit_stores_message_and_advances_revision(tmp_path):
    _write(tmp_path, "a.py", "x")
    tracker = FileTracker(root=str(tmp_path))
    tracker.commit(message="initial import")

    manifest = tracker.baseline.load()
    assert manifest["message"] == "initial import"
    assert manifest["revision"] != "initial"
    assert "committed_at" in manifest


def test_undo_restores_previous_baseline_without_touching_worktree(tmp_path):
    _write(tmp_path, "a.py", "v1")
    tracker = FileTracker(root=str(tmp_path))
    tracker.commit()
    _write(tmp_path, "a.py", "v2")
    tracker.commit()

    assert tracker.undo() is True
    assert tracker.read_baseline_snapshot("a.py").text == "v1"
    assert (tmp_path / "a.py").read_text() == "v2"
    assert tracker.undo() is True
    assert tracker.scan().files[0].status is ChangeStatus.ADDED
    assert tracker.undo() is False


def test_change_set_is_path_ordered_and_resolve_path_is_explicit(tmp_path):
    _write(tmp_path, "z.py", "z")
    _write(tmp_path, "a.py", "a")
    tracker = FileTracker(root=str(tmp_path))

    changes = tracker.scan()

    assert [change.path for change in changes] == [Path("a.py"), Path("z.py")]
    assert tracker.resolve_path(Path("a.py")) == tmp_path / "a.py"
