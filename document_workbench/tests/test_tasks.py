import json
import subprocess
import sys
from pathlib import Path

import pytest

import document_workbench.workbench as storage
from document_workbench import TaskResult, Workbench, WorkbenchError
from document_updater import (
    DocumentResult, InvalidManagedDocumentError, ResultKind,
    apply_results, get_fragment_markdown,
)


def _results(body: str) -> tuple[DocumentResult, ...]:
    return (DocumentResult("fragment", ResultKind.UPSERT, body),)


def test_task_pairs_recover_in_order_and_publish_only_documents(tmp_path: Path) -> None:
    root = tmp_path / ".workbench"
    workbench = Workbench.open(root)
    workbench.save_task("a.md", "B", _results("second"))
    workbench.save_task("a.md", "A", _results("first"))
    workbench.save_task("b.md", "A", ())
    workbench.close()
    resumed = Workbench.open(root)
    assert resumed.contains_task("a.md", "A")
    assert not resumed.contains_task("a.md", "missing")
    assert resumed.task_documents() == ("a.md", "b.md")
    assert tuple(resumed.task_results("a.md").items()) == (
        ("B", _results("second")), ("A", _results("first")),
    )
    assert resumed.task_results("b.md") == {"A": ()}
    assert resumed.task_results("unknown.md") == {}
    with pytest.raises(TypeError):
        resumed.task_results("a.md")["C"] = _results("changed")
    with pytest.raises(WorkbenchError, match="already completed"):
        resumed.save_task("a.md", "A", _results("duplicate"))
    resumed.save("a.md", "assembled A")
    resumed.close()
    resumed = Workbench.open(root)
    with pytest.raises(WorkbenchError, match="frozen"):
        resumed.save_task("c.md", "C", _results("late"))
    resumed.publish(tmp_path)
    assert (tmp_path / "a.md").read_text() == "assembled A"
    assert (tmp_path / "b.md").read_text() == ""
    assert not root.exists()
    assert sorted(path.name for path in tmp_path.iterdir()) == ["a.md", "b.md"]


@pytest.mark.parametrize("after_replace", [False, True])
def test_task_commit_recovers_at_atomic_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_replace: bool,
) -> None:
    root = tmp_path / "run"
    workbench = Workbench.open(root)
    original = storage.os.replace

    def fail(source, destination):
        if after_replace:
            original(source, destination)
        raise OSError("commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "replace", fail)
        with pytest.raises(OSError, match="commit failed"):
            workbench.save_task("a.md", "A", _results("result"))
    with pytest.raises(WorkbenchError, match="reopen"):
        workbench.task_results("a.md")
    resumed = Workbench.open(root)
    assert resumed.contains_task("a.md", "A") is after_replace
    if not after_replace:
        resumed.save_task("a.md", "A", _results("retried"))


def test_freezing_survives_failed_document_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "run"
    workbench = Workbench.open(root)
    workbench.save_task("a.md", "A", _results("result"))
    original = Path.open

    def fail(path, mode="r", *args, **kwargs):
        if mode == "wb":
            raise OSError("output failed")
        return original(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", fail)
        with pytest.raises(OSError, match="output failed"):
            workbench.save("a.md", "assembled")
    resumed = Workbench.open(root)
    with pytest.raises(WorkbenchError, match="frozen"):
        resumed.save_task("a.md", "B", _results("late"))
    resumed.save("a.md", "assembled")


@pytest.mark.parametrize("tasks,frozen", [
    ({"a.md": {}}, False),
    ({"../escape": {"A": "text"}}, False),
    ({"a.md": {"": "text"}}, False),
    ({"a.md": {"A": 3}}, False),
    ({"a.md": {"A": "text"}}, 1),
])
def test_invalid_task_state_is_rejected(tmp_path: Path, tasks, frozen) -> None:
    root = tmp_path / "run"
    Workbench.open(root).close()
    path = root / "state.json"
    state = json.loads(path.read_text())
    state.update(tasks=tasks, frozen=frozen)
    path.write_text(json.dumps(state))
    with pytest.raises(WorkbenchError):
        Workbench.open(root)


def test_schema_five_document_results_remain_readable(tmp_path: Path) -> None:
    root = tmp_path / "run"
    workbench = Workbench.open(root)
    workbench.save("a.md", "A")
    workbench.close()
    path = root / "state.json"
    state = json.loads(path.read_text())
    state["schema_version"] = 5
    del state["tasks"]
    del state["frozen"]
    path.write_text(json.dumps(state))
    resumed = Workbench.open(root)
    assert resumed.results() == {"a.md": "A"}
    with pytest.raises(WorkbenchError, match="frozen"):
        resumed.save_task("a.md", "late", _results("late"))
    resumed.publish(tmp_path / "published")


def test_task_pair_survives_process_interruption(tmp_path: Path) -> None:
    root = tmp_path / ".workbench"
    result = subprocess.run(
        [
            sys.executable, "-c",
            "import sys\n"
            "from document_workbench import Workbench\n"
            "from document_updater import DocumentResult, ResultKind\n"
            "w = Workbench.open(sys.argv[1])\n"
            "w.save_task('a.md', 'A:1', (DocumentResult('first', ResultKind.UPSERT, 'first'),))\n"
            "raise RuntimeError('interrupted')\n",
            str(root),
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "RuntimeError: interrupted" in result.stderr
    resumed = Workbench.open(root)
    assert resumed.contains_task("a.md", "A:1")
    resumed.save_task("a.md", "B:1", _results("second"))
    resumed.save("a.md", "\n".join(
        result.markdown
        for results in resumed.task_results("a.md").values()
        for result in results
        if result.markdown is not None
    ))
    resumed.publish(tmp_path)
    assert (tmp_path / "a.md").read_text() == "first\nsecond"


def test_fragment_results_round_trip_in_order(tmp_path: Path) -> None:
    root = tmp_path / "run"
    results = (
        DocumentResult("same", ResultKind.UPSERT, "\u4fdd\u7559\n\"quoted\""),
        DocumentResult("same", ResultKind.DELETE),
        DocumentResult("same", ResultKind.UPSERT, "New body"),
    )
    with Workbench.open(root) as workbench:
        workbench.save_task("a.md", "A", results)
    state = json.loads((root / "state.json").read_text())
    checkpoint = TaskResult("a.md", "A", results)
    assert state["tasks"]["a.md"]["A"] == checkpoint.to_dict()
    assert TaskResult.from_dict(checkpoint.to_dict()) == checkpoint
    with Workbench.open(root) as resumed:
        assert resumed.task_results("a.md") == {"A": results}


@pytest.mark.parametrize("results", ["text", [], [*_results("body")], ("text",)])
def test_invalid_task_input_does_not_commit(tmp_path: Path, results) -> None:
    with Workbench.open(tmp_path / "run") as workbench:
        with pytest.raises(TypeError, match="tuple of DocumentResult"):
            workbench.save_task("a.md", "A", results)
        assert not workbench.contains_task("a.md", "A")


@pytest.mark.parametrize("item", [
    {},
    {"fragment_id": "a", "kind": "unknown", "markdown": None},
    {"fragment_id": "a", "kind": "delete", "markdown": "invalid"},
    {"fragment_id": "a", "kind": "upsert", "markdown": 3},
])
def test_invalid_serialized_fragment_is_rejected(tmp_path: Path, item) -> None:
    root = tmp_path / "run"
    Workbench.open(root).close()
    path = root / "state.json"
    state = json.loads(path.read_text())
    state["tasks"] = {"a.md": {"A": {
        "document_key": "a.md", "task_id": "A", "results": [item],
    }}}
    path.write_text(json.dumps(state))
    with pytest.raises(WorkbenchError, match="invalid workbench state"):
        Workbench.open(root)


def test_schema_six_opaque_tasks_are_explicitly_rejected(tmp_path: Path) -> None:
    root = tmp_path / "run"
    Workbench.open(root).close()
    path = root / "state.json"
    state = json.loads(path.read_text())
    state.update(schema_version=6, tasks={"a.md": {"A": "opaque"}})
    path.write_text(json.dumps(state))
    with pytest.raises(WorkbenchError, match="unsupported workbench schema: 6"):
        Workbench.open(root)


@pytest.mark.parametrize("value", [
    None, [], {},
    {"document_key": "../escape", "task_id": "A", "results": []},
    {"document_key": "a.md", "task_id": "", "results": []},
    {"document_key": "a.md", "task_id": "A", "results": "invalid"},
    {"document_key": "a.md", "task_id": "A", "results": [
        {"fragment_id": 3, "kind": "delete", "markdown": None},
    ]},
])
def test_task_result_rejects_invalid_serialized_data(value) -> None:
    with pytest.raises(ValueError):
        TaskResult.from_dict(value)


def test_checkpoint_identity_must_match_storage_key(tmp_path: Path) -> None:
    root = tmp_path / "run"
    Workbench.open(root).close()
    path = root / "state.json"
    state = json.loads(path.read_text())
    state["tasks"] = {"a.md": {"A": TaskResult("b.md", "A", ()).to_dict()}}
    path.write_text(json.dumps(state))
    with pytest.raises(WorkbenchError, match="invalid workbench state"):
        Workbench.open(root)


def test_assemble_preserves_originals_and_orders_fragment_mutations(tmp_path: Path) -> None:
    document = tmp_path / "a.md"
    original = apply_results("# Manual\n", (
        DocumentResult("keep", ResultKind.UPSERT, "Keep me"),
        DocumentResult("remove", ResultKind.UPSERT, "Remove me"),
    ))
    document.write_text(original, encoding="utf-8")
    empty_original = "\n# Unchanged\n\n"
    (tmp_path / "empty.md").write_text(empty_original, encoding="utf-8")
    with Workbench.open(tmp_path / ".workbench") as workbench:
        workbench.save_task("a.md", "B", (
            DocumentResult("same", ResultKind.DELETE),
            DocumentResult("same", ResultKind.UPSERT, "Final"),
            DocumentResult("remove", ResultKind.DELETE),
        ))
        workbench.save_task("a.md", "A", (
            DocumentResult("same", ResultKind.UPSERT, "First"),
        ))
        workbench.save_task("new/nested.md", "A", _results("New"))
        workbench.save_task("empty.md", "A", ())
        workbench.save_task("missing.md", "A", ())
        workbench.assemble(tmp_path)
        outputs = workbench.results()
        assert outputs["a.md"].startswith("# Manual\n")
        assert get_fragment_markdown(outputs["a.md"], "keep") == "Keep me"
        assert get_fragment_markdown(outputs["a.md"], "remove") is None
        assert get_fragment_markdown(outputs["a.md"], "same") == "First"
        assert get_fragment_markdown(outputs["new/nested.md"], "fragment") == "New"
        assert outputs["empty.md"] == empty_original
        assert outputs["missing.md"] == ""
        assert document.read_text(encoding="utf-8") == original
        assert not (tmp_path / "new").exists()
        with pytest.raises(WorkbenchError, match="frozen"):
            workbench.save_task("a.md", "C", ())


@pytest.mark.parametrize("existing_document", [False, True])
def test_publish_preserves_task_save_order_across_recovery(
    tmp_path: Path, existing_document: bool,
) -> None:
    root = tmp_path / ".workbench"
    document = tmp_path / "a.md"
    if existing_document:
        document.write_text(apply_results("# Manual\n", _results("Original body")))
    with Workbench.open(root) as workbench:
        workbench.save_task("a.md", "Z", (
            DocumentResult("zeta", ResultKind.UPSERT, "Zeta body"),
            DocumentResult("fragment", ResultKind.UPSERT, "Earlier body"),
        ))
        workbench.save_task("a.md", "B", (
            DocumentResult("alpha", ResultKind.UPSERT, "Alpha body"),
        ))
        assert tuple(workbench.task_results("a.md")) == ("Z", "B")
    with Workbench.open(root) as resumed:
        assert tuple(resumed.task_results("a.md")) == ("Z", "B")
        resumed.save_task("a.md", "A", (
            DocumentResult("fragment", ResultKind.UPSERT, "Latest body"),
            DocumentResult("omega", ResultKind.UPSERT, "Omega body"),
        ))
    with Workbench.open(root) as resumed:
        assert tuple(resumed.task_results("a.md")) == ("Z", "B", "A")
        resumed.publish(tmp_path)
    published = document.read_text()
    assert get_fragment_markdown(published, "fragment") == "Latest body"
    bodies = (
        ("Latest body", "Zeta body", "Alpha body", "Omega body")
        if existing_document
        else ("Zeta body", "Latest body", "Alpha body", "Omega body")
    )
    positions = tuple(published.index(body) for body in bodies)
    assert positions == tuple(sorted(positions))
    if existing_document:
        assert published.startswith("# Manual\n")


def test_assemble_retry_skips_saved_documents_after_output_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / ".workbench"
    workbench = Workbench.open(root)
    for key in ("a.md", "b.md"):
        workbench.save_task(key, "A", _results(key))
    original_open = Path.open

    def fail(path, mode="r", *args, **kwargs):
        if path == root / "outputs/b.md" and mode == "wb":
            raise OSError("output failed")
        return original_open(path, mode, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", fail)
        with pytest.raises(OSError, match="output failed"):
            workbench.assemble(tmp_path)
    workbench.close()
    resumed = Workbench.open(root)
    assert resumed.contains("a.md")
    assert not resumed.contains("b.md")
    original_apply = storage.apply_results
    merged = []

    def apply(markdown, results):
        merged.append(results)
        return original_apply(markdown, results)

    monkeypatch.setattr(storage, "apply_results", apply)
    resumed.assemble(tmp_path)
    assert merged == [_results("b.md")]
    resumed.publish(tmp_path)
    assert get_fragment_markdown((tmp_path / "b.md").read_text(), "fragment") == "b.md"


def test_assemble_propagates_invalid_original_without_completing_document(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(
        '<!-- codegraph:fragment:end fragment_id="a" -->\n', encoding="utf-8",
    )
    with Workbench.open(tmp_path / ".workbench") as workbench:
        workbench.save_task("a.md", "A", ())
        with pytest.raises(InvalidManagedDocumentError):
            workbench.assemble(tmp_path)
        assert not workbench.contains("a.md")
        assert workbench.contains_task("a.md", "A")


@pytest.mark.parametrize("kind", ["root", "parent", "file", "dangling", "directory"])
def test_assemble_rejects_non_regular_document_paths(tmp_path: Path, kind: str) -> None:
    root = tmp_path / ".workbench"
    documents = tmp_path / "documents"
    documents.mkdir()
    key = "a.md"
    if kind == "root":
        link = tmp_path / "link"
        link.symlink_to(documents, target_is_directory=True)
        documents = link
    elif kind == "parent":
        (documents / "link").symlink_to(tmp_path, target_is_directory=True)
        key = "link/a.md"
    elif kind in {"file", "dangling"}:
        target = tmp_path / "original.md"
        if kind == "file":
            target.write_text("Original", encoding="utf-8")
        (documents / key).symlink_to(target)
    else:
        (documents / key).mkdir()
    with Workbench.open(root) as workbench:
        workbench.save_task(key, "A", ())
        with pytest.raises(WorkbenchError):
            workbench.assemble(documents)
        assert not workbench.contains(key)


def test_assemble_rejects_workbench_as_document_input(tmp_path: Path) -> None:
    root = tmp_path / ".workbench"
    with Workbench.open(root) as workbench:
        workbench.save_task(".workbench/state.json", "A", ())
        for document_root in (root, root / "nested", tmp_path):
            with pytest.raises(ValueError, match="workbench"):
                workbench.assemble(document_root)


def test_publish_assembles_all_documents_before_writing_destinations(tmp_path: Path) -> None:
    root = tmp_path / ".workbench"
    original_a = "# Original A\n"
    (tmp_path / "a.md").write_text(original_a, encoding="utf-8")
    document_b = tmp_path / "b.md"
    invalid_b = '<!-- codegraph:fragment:end fragment_id="a" -->\n'
    document_b.write_text(invalid_b, encoding="utf-8")
    with Workbench.open(root) as workbench:
        workbench.save_task("a.md", "A", _results("Updated A"))
        workbench.save_task("b.md", "A", _results("Updated B"))
        with pytest.raises(InvalidManagedDocumentError):
            workbench.publish(tmp_path)
        assert workbench.contains("a.md")
        assert not workbench.contains("b.md")
    assert (tmp_path / "a.md").read_text() == original_a
    assert document_b.read_text() == invalid_b
    document_b.write_text("# Original B\n", encoding="utf-8")
    with Workbench.open(root) as resumed:
        resumed.publish(tmp_path)
    assert get_fragment_markdown((tmp_path / "a.md").read_text(), "fragment") == "Updated A"
    assert get_fragment_markdown(document_b.read_text(), "fragment") == "Updated B"
    assert not root.exists()
