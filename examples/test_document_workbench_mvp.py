from pathlib import Path

import pytest

import document_workbench.workbench as storage
from document_workbench import Workbench
from filetracker import FileTracker

from examples import document_workbench_mvp as example
from examples import document_workbench_support as support


def _project(tmp_path: Path) -> tuple[Path, Path, FileTracker]:
    source_root = tmp_path / "src"
    source_root.mkdir()
    example._write_initial_sources(source_root)
    tracker = FileTracker(str(source_root))
    tracker.commit()
    example._write_changed_sources(source_root)
    return source_root, tmp_path / "workbench", tracker


def test_reanalysis_after_processing_failure_skips_successful_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root, root, tracker = _project(tmp_path)
    processed = []
    analyses = []
    original_analyze = example.analyze_documents

    def analyze(source_root, changes):
        analyses.append(changes)
        return original_analyze(source_root, changes)

    monkeypatch.setattr(example, "analyze_documents", analyze)

    def process(key, changes, markdown):
        processed.append(key)
        if key == "docs/orders.md":
            raise RuntimeError("generation failed")
        return example.generate_document(changes, markdown)

    with pytest.raises(RuntimeError, match="generation failed"):
        example.run_batch(source_root, tmp_path, root, tracker, process=process)
    with Workbench.open(root) as workbench:
        assert workbench.contains("docs/customers.md")
        assert not workbench.contains("docs/orders.md")

    def resume(key, changes, markdown):
        processed.append(key)
        return example.generate_document(changes, markdown)

    example.run_batch(source_root, tmp_path, root, tracker, process=resume)
    assert len(analyses) == 2
    assert processed == ["docs/customers.md", "docs/orders.md", "docs/orders.md"]
    assert not root.exists()
    assert tracker.scan().has_changes
    with Workbench.open(root) as workbench:
        assert workbench.results() == {}


def test_delete_removes_fragment_without_source_data(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    example._write_initial_sources(source_root)
    tracker = FileTracker(str(source_root))
    example.run_batch(source_root, tmp_path, tmp_path / "workbench", tracker)
    tracker.commit()
    (source_root / "orders.pkb").unlink()
    documents = support.analyze_documents(source_root, tracker.scan())
    assert tuple(documents) == ("docs/orders.md",)
    assert documents["docs/orders.md"][0].new_function is None
    assert example.generate_document(
        documents["docs/orders.md"], support.read_document(tmp_path / "docs/orders.md")
    ) == b""


def test_new_generation_preserves_existing_document_text(tmp_path: Path) -> None:
    source_root, root, tracker = _project(tmp_path)
    document = tmp_path / "docs/orders.md"
    document.parent.mkdir()
    original = "# Original\n\n\u4fdd\u7559\u539f\u6587\u3002\n"
    document.write_text(original, encoding="utf-8")
    example.run_batch(source_root, tmp_path, root, tracker)
    markdown = document.read_text(encoding="utf-8")
    assert markdown.startswith(original)
    assert "status" in markdown


def test_partial_publication_preserves_results_and_does_not_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root, root, tracker = _project(tmp_path)
    original_replace = storage.os.replace

    def fail_publish(source, path):
        if Path(path) == tmp_path / "docs/orders.md":
            raise OSError("publication failed")
        original_replace(source, path)

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "replace", fail_publish)
        with pytest.raises(OSError, match="publication failed"):
            example.run_batch(source_root, tmp_path, root, tracker)
    assert tracker.scan().has_changes
    with Workbench.open(root) as workbench:
        assert len(workbench.results()) == 2
    example.run_batch(
        source_root, tmp_path, root, tracker,
        process=lambda *args: pytest.fail("must not generate again"),
    )
    assert not root.exists()


def test_analysis_failure_retains_successful_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root, root, tracker = _project(tmp_path)
    with Workbench.open(root) as workbench:
        workbench.save("docs/customers.md", b"saved")

    def fail_analysis(*args):
        raise RuntimeError("analysis failed")

    monkeypatch.setattr(example, "analyze_documents", fail_analysis)
    with pytest.raises(RuntimeError, match="analysis failed"):
        example.run_batch(source_root, tmp_path, root, tracker)
    with Workbench.open(root) as workbench:
        assert workbench.results() == {"docs/customers.md": b"saved"}
    assert tracker.scan().has_changes


def test_publication_never_commits_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root, root, tracker = _project(tmp_path)
    monkeypatch.setattr(tracker, "commit", lambda *args, **kwargs: pytest.fail("commit is separate"))
    example.run_batch(source_root, tmp_path, root, tracker)
    assert tracker.scan().has_changes
    assert not root.exists()


def test_workbench_cannot_replace_project_or_live_inside_tracked_sources(tmp_path: Path) -> None:
    source_root, _, tracker = _project(tmp_path)
    for root in (tmp_path, source_root / "workbench"):
        with pytest.raises(ValueError, match="workbench"):
            example.run_batch(source_root, tmp_path, root, tracker)
    assert (source_root / "orders.pkb").is_file()
