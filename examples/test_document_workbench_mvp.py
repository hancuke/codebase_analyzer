from pathlib import Path

import pytest

from change_analyzer import FunctionChange
from document_updater import DocumentResult
from document_workbench import DocumentRun
from filetracker import FileTracker, RevisionConflictError

from examples import document_workbench_mvp as example


def test_resume_uses_fixed_inputs_and_skips_completed_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    example._write_initial_sources(source_root)
    tracker = FileTracker(str(source_root))
    tracker.commit()
    example._write_changed_sources(source_root)
    run_root = tmp_path / "run"

    def capture(inputs_root: Path) -> None:
        example._capture_run_inputs(inputs_root, source_root, tmp_path, tracker)

    with DocumentRun.open_or_create(run_root, capture_inputs=capture):
        pass

    assert example.run_pending_documents(run_root, stop_after=1) == (
        "docs/customers.md",
    )
    for path in source_root.glob("*.pkb"):
        path.unlink()

    rendered: list[str] = []
    original_renderer = example._function_result

    def render(change: FunctionChange) -> DocumentResult:
        rendered.append(change.function_id)
        return original_renderer(change)

    monkeypatch.setattr(example, "_function_result", render)
    assert example.run_pending_documents(run_root) == ("docs/orders.md",)
    assert len(rendered) == 1
    assert example.run_pending_documents(run_root) == ()
    assert len(rendered) == 1
    with DocumentRun.open(run_root) as run:
        assert "status" in run.output_path("docs/orders.md").read_text(encoding="utf-8")


def test_publication_does_not_commit_source_changes_since_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_copy = example.shutil.copyfile

    def publish(source: Path, destination: Path) -> None:
        original_copy(source, destination)
        source_file = destination.parent.parent / "src" / "orders.pkb"
        source_file.write_text("changed after capture", encoding="utf-8")

    monkeypatch.setattr(example.shutil, "copyfile", publish)
    with pytest.raises(RevisionConflictError, match="Working tree changed"):
        example.main()
