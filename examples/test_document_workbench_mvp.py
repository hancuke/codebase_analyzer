from pathlib import Path

import pytest

import document_workbench.workbench as storage
from document_workbench import Workbench
from document_updater import (
    DocumentResult, ResultKind, apply_result, get_fragment_markdown,
)
from filetracker import FileTracker

from examples import document_workbench_mvp as example


def _project(tmp_path: Path) -> tuple[Path, Path, FileTracker]:
    source_root = tmp_path / "src"
    source_root.mkdir()
    example._write_initial_sources(source_root)
    tracker = FileTracker(str(source_root))
    tracker.commit()
    example._write_changed_sources(source_root)
    return source_root, tmp_path / ".workbench", tracker


def test_reanalysis_after_processing_failure_skips_successful_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root, root, tracker = _project(tmp_path)
    processed = []
    analyses = []
    original_analyze = example.analyze_documents
    originals = {
        "docs/customers.md": "# Customers\n",
        "docs/orders.md": "# Orders\n",
    }
    for key, markdown in originals.items():
        document = tmp_path / key
        document.parent.mkdir(parents=True, exist_ok=True)
        document.write_text(markdown, encoding="utf-8")

    def analyze(source_root, changes):
        analyses.append(changes)
        return original_analyze(source_root, changes)

    monkeypatch.setattr(example, "analyze_documents", analyze)

    def process(key, changes, markdown):
        processed.append(key)
        assert markdown == originals[key]
        if key == "docs/orders.md":
            raise RuntimeError("generation failed")
        return example.generate_fragments(changes)

    with pytest.raises(RuntimeError, match="generation failed"):
        example.run_batch(source_root, tmp_path, tracker, generate=process)
    with Workbench.open(root) as workbench:
        assert workbench.contains("docs/customers.md")
        assert not workbench.contains("docs/orders.md")
    for key, markdown in originals.items():
        assert (tmp_path / key).read_text(encoding="utf-8") == markdown

    def resume(key, changes, markdown):
        processed.append(key)
        assert markdown == originals[key]
        return example.generate_fragments(changes)

    example.run_batch(source_root, tmp_path, tracker, generate=resume)
    assert len(analyses) == 2
    assert processed == ["docs/customers.md", "docs/orders.md", "docs/orders.md"]
    assert not root.exists()
    for key, markdown in originals.items():
        published = (tmp_path / key).read_text(encoding="utf-8")
        assert published.startswith(markdown)
        assert published != markdown
    assert tracker.scan().has_changes
    with Workbench.open(root) as workbench:
        assert workbench.results() == {}


def test_delete_removes_fragment_without_source_data(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    source_root.mkdir()
    example._write_initial_sources(source_root)
    tracker = FileTracker(str(source_root))
    example.run_batch(source_root, tmp_path, tracker)
    tracker.commit()
    (source_root / "orders.pkb").unlink()
    documents = example.analyze_documents(source_root, tracker.scan())
    assert tuple(documents) == ("docs/orders.md",)
    assert documents["docs/orders.md"][0].new_function is None
    change = documents["docs/orders.md"][0]
    assert example.generate_fragments(documents["docs/orders.md"]) == (
        DocumentResult(f"function:{change.function_id}", ResultKind.DELETE),
    )
    example.run_batch(source_root, tmp_path, tracker)
    document = tmp_path / "docs/orders.md"
    assert document.is_file()
    assert document.read_text(encoding="utf-8") == ""


def test_new_generation_preserves_existing_document_text(tmp_path: Path) -> None:
    source_root, root, tracker = _project(tmp_path)
    document = tmp_path / "docs/orders.md"
    document.parent.mkdir()
    original = "# Original\n\n\u4fdd\u7559\u539f\u6587\u3002\n"
    changes = example.analyze_documents(source_root, tracker.scan())["docs/orders.md"]
    fragment_id = f"function:{changes[0].function_id}"
    markdown = apply_result(
        original, DocumentResult(fragment_id, ResultKind.UPSERT, "Old description")
    )
    markdown = apply_result(
        markdown, DocumentResult("manual:notes", ResultKind.UPSERT, "Keep these notes")
    )
    document.write_text(markdown, encoding="utf-8")
    example.run_batch(source_root, tmp_path, tracker)
    markdown = document.read_text(encoding="utf-8")
    assert markdown.startswith(original)
    assert get_fragment_markdown(markdown, "manual:notes") == "Keep these notes"
    updated_fragment = get_fragment_markdown(markdown, fragment_id)
    assert updated_fragment is not None
    assert "status" in updated_fragment
    assert "Old description" not in updated_fragment


def test_fragment_generation_is_independent_of_document_mutation(tmp_path: Path) -> None:
    source_root, _, tracker = _project(tmp_path)
    changes = example.analyze_documents(source_root, tracker.scan())["docs/orders.md"]
    results = example.generate_fragments(changes)
    assert len(results) == 1
    result = results[0]
    assert result.fragment_id == f"function:{changes[0].function_id}"
    assert result.kind is ResultKind.UPSERT
    assert result.markdown is not None
    assert "status" in result.markdown
    assert "<!-- codegraph:" not in result.markdown


def test_custom_generation_returns_fragments_for_shared_merge(tmp_path: Path) -> None:
    source_root, root, tracker = _project(tmp_path)
    document = tmp_path / "docs/orders.md"
    document.parent.mkdir()
    original = apply_result(
        "# Manual introduction\n",
        DocumentResult("manual:notes", ResultKind.UPSERT, "Keep these notes"),
    )
    document.write_text(original, encoding="utf-8")

    def generate(key, changes, markdown):
        if key == "docs/orders.md":
            assert markdown == original
        return (
            DocumentResult("custom:summary", ResultKind.UPSERT, "Generated summary"),
        )

    example.run_batch(source_root, tmp_path, tracker, generate=generate)
    markdown = document.read_text(encoding="utf-8")
    assert markdown.startswith("# Manual introduction\n")
    assert get_fragment_markdown(markdown, "manual:notes") == "Keep these notes"
    assert get_fragment_markdown(markdown, "custom:summary") == "Generated summary"


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
            example.run_batch(source_root, tmp_path, tracker)
    assert tracker.scan().has_changes
    with Workbench.open(root) as workbench:
        assert len(workbench.results()) == 2
    example.run_batch(
        source_root, tmp_path, tracker,
        generate=lambda *args: pytest.fail("must not generate again"),
    )
    assert not root.exists()


def test_analysis_failure_retains_successful_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root, root, tracker = _project(tmp_path)
    with Workbench.open(root) as workbench:
        workbench.save("docs/customers.md", "saved")

    def fail_analysis(*args):
        raise RuntimeError("analysis failed")

    monkeypatch.setattr(example, "analyze_documents", fail_analysis)
    with pytest.raises(RuntimeError, match="analysis failed"):
        example.run_batch(source_root, tmp_path, tracker)
    with Workbench.open(root) as workbench:
        assert workbench.results() == {"docs/customers.md": "saved"}
    assert tracker.scan().has_changes


def test_publication_never_commits_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_root, root, tracker = _project(tmp_path)
    monkeypatch.setattr(tracker, "commit", lambda *args, **kwargs: pytest.fail("commit is separate"))
    example.run_batch(source_root, tmp_path, tracker)
    assert tracker.scan().has_changes
    assert not root.exists()


def test_workbench_cannot_replace_project_or_live_inside_tracked_sources(tmp_path: Path) -> None:
    source_root, _, tracker = _project(tmp_path)
    for document_root in (source_root, source_root / "documents"):
        with pytest.raises(ValueError, match="workbench"):
            example.run_batch(source_root, document_root, tracker)
    assert (source_root / "orders.pkb").is_file()


def test_prepared_workbench_can_be_published_later(tmp_path: Path) -> None:
    source_root, root, tracker = _project(tmp_path)
    workbench = example.prepare_batch(source_root, tmp_path, tracker)
    assert root.is_dir()
    assert not (root / ".lock").exists()
    assert not (tmp_path / "docs").exists()
    assert tuple(workbench.results()) == ("docs/customers.md", "docs/orders.md")

    workbench.publish(tmp_path)

    assert not root.exists()
    assert (tmp_path / "docs/orders.md").is_file()
    assert tracker.scan().has_changes


def test_a_partial_tasks_resume_and_b_merges_without_reassembly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".workbench"
    document = tmp_path / "a.md"
    document.write_text("# Manual\n", encoding="utf-8")
    workbench = Workbench.open(root)
    workbench.save_task("a.md", "A:1", (
        DocumentResult("a1", ResultKind.UPSERT, "First"),
    ))
    workbench.close()
    workbench = Workbench.open(root)
    assert workbench.contains_task("a.md", "A:1")
    for task_id, fragment_id in (("A:2", "a2"), ("B:1", "b1")):
        workbench.save_task("a.md", task_id, (
            DocumentResult(fragment_id, ResultKind.UPSERT, task_id),
        ))
    original_replace = storage.os.replace

    def fail(source, destination):
        if Path(destination) == document:
            raise OSError("publication failed")
        original_replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "replace", fail)
        with pytest.raises(OSError, match="publication failed"):
            workbench.publish(tmp_path)
    resumed = Workbench.open(root)
    with monkeypatch.context() as patch:
        patch.setattr(storage, "apply_results", lambda *args: pytest.fail("must not reassemble"))
        resumed.publish(tmp_path)
    markdown = document.read_text(encoding="utf-8")
    assert markdown.startswith("# Manual\n")
    assert get_fragment_markdown(markdown, "a1") == "First"
    assert get_fragment_markdown(markdown, "a2") == "A:2"
    assert get_fragment_markdown(markdown, "b1") == "B:1"
