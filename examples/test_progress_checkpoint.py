from pathlib import Path
from unittest.mock import Mock

import pytest

import progress_checkpoint as checkpoint
from progress_checkpoint import ProgressCheckpoint


def test_success_survives_restart_and_replaces_stale_record(tmp_path: Path) -> None:
    path = tmp_path / "progress.json"
    progress = ProgressCheckpoint(path)
    assert not progress.matches("job-1", "input-v1", "output-v1")

    progress.record_success("job-1", "input-v1", "output-v1")
    restarted = ProgressCheckpoint(path)
    assert restarted.matches("job-1", "input-v1", "output-v1")
    assert not restarted.matches("job-1", "input-v2", "output-v1")
    assert not restarted.matches("job-1", "input-v1", "output-v2")
    restarted.record_success("job-2", "other-input", "other-output")
    restarted.record_success("job-1", "input-v2", "output-v2")

    restarted = ProgressCheckpoint(path)
    assert restarted.matches("job-1", "input-v2", "output-v2")
    assert restarted.matches("job-2", "other-input", "other-output")
    assert not restarted.matches("job-1", "input-v1", "output-v1")
    restarted.clear()
    assert not restarted.matches("job-1", "input-v2", "output-v2")
    assert not ProgressCheckpoint(path).matches("job-2", "other-input", "other-output")


@pytest.mark.parametrize("operation", ["record", "clear"])
def test_failed_write_preserves_memory_and_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str,
) -> None:
    path = tmp_path / "progress.json"
    progress = ProgressCheckpoint(path)
    progress.record_success("job", "old-input", "old-output")
    previous = path.read_bytes()
    original_write = checkpoint.atomic_write_text
    monkeypatch.setattr(
        checkpoint, "atomic_write_text",
        Mock(side_effect=OSError("injected checkpoint failure")),
    )

    with pytest.raises(OSError, match="checkpoint failure"):
        if operation == "record":
            progress.record_success("job", "new-input", "new-output")
        else:
            progress.clear()

    assert progress.matches("job", "old-input", "old-output")
    assert not progress.matches("job", "new-input", "new-output")
    assert path.read_bytes() == previous
    assert ProgressCheckpoint(path).matches("job", "old-input", "old-output")

    monkeypatch.setattr(checkpoint, "atomic_write_text", original_write)
    progress.record_success("job", "new-input", "new-output")
    assert progress.matches("job", "new-input", "new-output")
