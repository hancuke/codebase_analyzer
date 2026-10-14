from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import document_workbench.workbench as run_storage

from document_workbench import (
    DocumentRun,
    InvalidUnitIdError,
    UnsupportedWorkbenchVersionError,
    WorkbenchBusyError,
    WorkbenchCorruptError,
    WorkbenchPhase,
    WorkbenchStateError,
)


def _capture_inputs(root: Path) -> None:
    (root / "sources").mkdir()
    (root / "sources" / "module.txt").write_text("fixed source", encoding="utf-8")


@pytest.mark.parametrize("sync", [False, True])
def test_windows_digest_uses_writable_handle_only_when_syncing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, sync: bool
) -> None:
    path = tmp_path / "document.md"
    content = b"complete document"
    path.write_bytes(content)
    original_open = Path.open
    modes: list[str] = []

    def open_file(self: Path, mode: str = "r", **kwargs):
        modes.append(mode)
        return original_open(self, mode, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(run_storage.os, "name", "nt")
        patch.setattr(Path, "open", open_file)
        result = DocumentRun._digest_file(path, sync=sync)

    assert modes == ["r+b" if sync else "rb"]
    assert result == (len(content), hashlib.sha256(content).hexdigest())
    assert path.read_bytes() == content


def test_open_create_process_and_resume_skips_completed_units(tmp_path: Path) -> None:
    root = tmp_path / "run"
    capture_calls = 0

    def capture(inputs: Path) -> None:
        nonlocal capture_calls
        capture_calls += 1
        _capture_inputs(inputs)

    with DocumentRun.open_or_create(root, capture_inputs=capture) as workbench:
        assert workbench.snapshot_root.joinpath("sources/module.txt").read_text() == "fixed source"
        assert workbench.status().phase is WorkbenchPhase.SNAPSHOT_READY
        with pytest.raises(WorkbenchStateError, match="register the unit plan"):
            workbench.pending_units()

        workbench.register_units(("docs/B.md", "docs/A.md"))
        assert workbench.pending_units() == ("docs/A.md", "docs/B.md")
        output = workbench.prepare_unit_output("docs/A.md")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text("# A", encoding="utf-8")
        completion = workbench.mark_unit_completed("docs/A.md")

        assert completion.size_bytes == 3
        assert completion.sha256 == hashlib.sha256(b"# A").hexdigest()
        assert workbench.pending_units() == ("docs/B.md",)
        assert workbench.output_path("docs/A.md").read_text() == "# A"

    def unexpected_capture(_: Path) -> None:
        pytest.fail("capture callback must not run when resuming")

    with DocumentRun.open_or_create(root, capture_inputs=unexpected_capture) as resumed:
        assert capture_calls == 1
        assert resumed.pending_units() == ("docs/B.md",)
        output = resumed.prepare_unit_output("docs/B.md")
        output.write_text("# B", encoding="utf-8")
        resumed.mark_unit_completed("docs/B.md")
        status = resumed.status()

    assert status.phase is WorkbenchPhase.COMPLETED
    assert status.is_complete
    assert status.pending_unit_ids == ()
    assert tuple(item.unit_id for item in status.completions) == (
        "docs/A.md",
        "docs/B.md",
    )

    with DocumentRun.open(root) as completed:
        assert completed.status().is_complete
        assert completed.pending_units() == ()


def test_register_empty_plan_is_complete_but_unregistered_is_not(tmp_path: Path) -> None:
    with DocumentRun.open_or_create(
        tmp_path / "run", capture_inputs=_capture_inputs
    ) as workbench:
        status = workbench.status()
        assert status.phase is WorkbenchPhase.SNAPSHOT_READY
        assert not status.is_complete
        workbench.register_units(())
        assert workbench.status().phase is WorkbenchPhase.COMPLETED
        assert workbench.status().is_complete
        assert workbench.pending_units() == ()


def test_register_same_plan_is_idempotent_and_changed_plan_fails(
    tmp_path: Path,
) -> None:
    with DocumentRun.open_or_create(
        tmp_path / "run", capture_inputs=_capture_inputs
    ) as workbench:
        workbench.register_units(("docs/A.md", "docs/B.md"))
        workbench.register_units(("docs/B.md", "docs/A.md"))
        with pytest.raises(WorkbenchStateError, match="do not match"):
            workbench.register_units(("docs/A.md",))


@pytest.mark.parametrize(
    "unit_ids",
    [
        ("../escape.md",),
        ("/absolute.md",),
        ("C:/absolute.md",),
        (r"docs\escape.md",),
        ("docs//empty.md",),
        ("docs/./dot.md",),
        ("docs/a.md", "docs/a.md/child"),
        ("docs/a.md", "DOCS/A.MD"),
        ("docs/a.md", "docs/a.md"),
    ],
)
def test_rejects_invalid_or_colliding_unit_ids(
    tmp_path: Path, unit_ids: tuple[str, ...]
) -> None:
    with DocumentRun.open_or_create(
        tmp_path / "run", capture_inputs=_capture_inputs
    ) as workbench:
        with pytest.raises(InvalidUnitIdError):
            workbench.register_units(unit_ids)


def test_interrupted_unit_output_is_replaced_on_retry(tmp_path: Path) -> None:
    root = tmp_path / "run"
    with DocumentRun.open_or_create(root, capture_inputs=_capture_inputs) as workbench:
        workbench.register_units(("docs/A.md",))
        failed_attempt = workbench.prepare_unit_output("docs/A.md")
        failed_attempt.write_text("partial output", encoding="utf-8")

    with DocumentRun.open(root) as resumed:
        assert resumed.pending_units() == ("docs/A.md",)
        retry = resumed.prepare_unit_output("docs/A.md")
        assert not retry.exists()
        retry.write_text("complete output", encoding="utf-8")
        resumed.mark_unit_completed("docs/A.md")


def test_unit_must_be_pending_and_have_output_before_completion(
    tmp_path: Path,
) -> None:
    with DocumentRun.open_or_create(
        tmp_path / "run", capture_inputs=_capture_inputs
    ) as workbench:
        workbench.register_units(("docs/A.md",))
        with pytest.raises(WorkbenchStateError, match="does not exist"):
            workbench.mark_unit_completed("docs/A.md")
        with pytest.raises(WorkbenchStateError, match="unknown unit"):
            workbench.prepare_unit_output("docs/unknown.md")

        output = workbench.prepare_unit_output("docs/A.md")
        output.write_text("# A", encoding="utf-8")
        workbench.mark_unit_completed("docs/A.md")
        with pytest.raises(WorkbenchStateError, match="already completed"):
            workbench.prepare_unit_output("docs/A.md")


def test_completed_output_tampering_is_detected(tmp_path: Path) -> None:
    root = tmp_path / "run"
    with DocumentRun.open_or_create(root, capture_inputs=_capture_inputs) as workbench:
        workbench.register_units(("docs/A.md",))
        output = workbench.prepare_unit_output("docs/A.md")
        output.write_text("# A", encoding="utf-8")
        workbench.mark_unit_completed("docs/A.md")
        output.write_text("# changed", encoding="utf-8")
        with pytest.raises(WorkbenchCorruptError, match="integrity"):
            workbench.output_path("docs/A.md")


def test_pending_query_rejects_tampered_completed_output(tmp_path: Path) -> None:
    with DocumentRun.open_or_create(
        tmp_path / "run", capture_inputs=_capture_inputs
    ) as workbench:
        workbench.register_units(("docs/A.md", "docs/B.md"))
        output = workbench.prepare_unit_output("docs/A.md")
        output.write_text("# A", encoding="utf-8")
        workbench.mark_unit_completed("docs/A.md")
        output.write_text("# tampered", encoding="utf-8")

        with pytest.raises(WorkbenchCorruptError, match="integrity"):
            workbench.pending_units()


def test_snapshot_tampering_is_detected_after_reopen(tmp_path: Path) -> None:
    root = tmp_path / "run"
    with DocumentRun.open_or_create(root, capture_inputs=_capture_inputs) as workbench:
        workbench.register_units(())
        (workbench.snapshot_root / "sources/module.txt").write_text(
            "changed", encoding="utf-8"
        )

    with pytest.raises(WorkbenchCorruptError, match="snapshot"):
        DocumentRun.open(root)


def test_capture_failure_can_be_retried(tmp_path: Path) -> None:
    root = tmp_path / "run"

    def fail_after_writing(inputs: Path) -> None:
        assert inputs == root / "inputs"
        (inputs / "partial.txt").write_text("partial", encoding="utf-8")
        raise RuntimeError("capture failed")

    with pytest.raises(RuntimeError, match="capture failed"):
        DocumentRun.open_or_create(root, capture_inputs=fail_after_writing)

    with DocumentRun.open_or_create(root, capture_inputs=_capture_inputs) as run:
        assert (
            run.snapshot_root.joinpath("sources/module.txt").read_text()
            == "fixed source"
        )


def test_uncommitted_snapshot_is_replaced_on_retry(tmp_path: Path) -> None:
    root = tmp_path / "run"
    partial_snapshot = root / "inputs"
    partial_snapshot.mkdir(parents=True)
    (partial_snapshot / "partial.txt").write_text("partial", encoding="utf-8")
    temporary_manifest = root / ".manifest-interrupted.tmp"
    temporary_manifest.write_text('{"schema_version":', encoding="utf-8")

    with DocumentRun.open_or_create(root, capture_inputs=_capture_inputs) as run:
        assert sorted(path.name for path in run.snapshot_root.iterdir()) == ["sources"]
        assert not temporary_manifest.exists()


def test_manifest_commit_failure_can_be_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "run"

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("manifest commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(run_storage.os, "replace", fail_replace)
        with pytest.raises(OSError, match="manifest commit failed"):
            DocumentRun.open_or_create(root, capture_inputs=_capture_inputs)

    assert not (root / "manifest.json").exists()
    (root / "inputs/sources/module.txt").write_text("uncommitted", encoding="utf-8")
    with DocumentRun.open_or_create(root, capture_inputs=_capture_inputs) as run:
        assert (
            run.snapshot_root.joinpath("sources/module.txt").read_text()
            == "fixed source"
        )


@pytest.mark.parametrize("reserved_path", ["inputs", ".manifest-interrupted.tmp"])
def test_uncommitted_paths_cannot_be_symlinks(
    tmp_path: Path, reserved_path: str
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    root = tmp_path / "run"
    root.mkdir()
    (root / reserved_path).symlink_to(outside, target_is_directory=True)

    with pytest.raises(WorkbenchCorruptError, match="must be a real directory|regular file"):
        DocumentRun.open_or_create(root, capture_inputs=_capture_inputs)
    assert marker.read_text(encoding="utf-8") == "keep"


def test_unexpected_run_data_without_manifest_is_not_reused(tmp_path: Path) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "unexpected.txt").write_text("unknown", encoding="utf-8")

    with pytest.raises(WorkbenchCorruptError, match="no manifest"):
        DocumentRun.open_or_create(root, capture_inputs=_capture_inputs)


def test_workbench_lock_rejects_concurrent_writer(tmp_path: Path) -> None:
    root = tmp_path / "run"
    with DocumentRun.open_or_create(root, capture_inputs=_capture_inputs):
        with pytest.raises(WorkbenchBusyError):
            DocumentRun.open(root)


def test_unsupported_schema_is_not_reset(tmp_path: Path) -> None:
    root = tmp_path / "run"
    with DocumentRun.open_or_create(root, capture_inputs=_capture_inputs):
        pass

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(UnsupportedWorkbenchVersionError):
        DocumentRun.open_or_create(root, capture_inputs=_capture_inputs)


def test_symlinks_in_input_snapshot_are_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("external", encoding="utf-8")

    def capture(inputs: Path) -> None:
        (inputs / "link.txt").symlink_to(outside)

    with pytest.raises(WorkbenchCorruptError, match="non-regular"):
        DocumentRun.open_or_create(tmp_path / "run", capture_inputs=capture)
