from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path


examples_path = Path(__file__).resolve().parents[2] / "examples"
if str(examples_path) not in sys.path:
    sys.path.insert(0, str(examples_path))

import pytest

from change_analyzer import EntryChange, analyze_changes, entry_changes
from codegraph import SourceFile, VbaAnalyzer
from documentation_lifecycle import (
    DemoLlmClient,
    DocumentPublisher,
    entry_document_key,
    generate_entry_document,
    generate_ui_document,
    load_vba_sources,
    main,
    synchronize,
    write_source,
)
from document_updater import DocumentResult, ResultKind
from filetracker import FileTracker


def test_documentation_lifecycle_runs_end_to_end(capsys) -> None:
    main()

    output = capsys.readouterr().out
    assert "Affected entries: ['vba:frmOrder:bSave_Click']" in output
    assert "Affected documents: ['docs/frmOrder.md']" in output
    assert "Changed documents: ['docs/frmOrder.md']" in output
    assert "This generated fragment documents `vba:frmOrder:bSave_Click`." in output
    assert "Document exists: False" in output


def test_publisher_skips_empty_document_result_group_without_writing(
    tmp_path: Path,
) -> None:
    changed_documents = DocumentPublisher(tmp_path).publish({"docs/order.md": ()})

    assert changed_documents == ()
    assert not (tmp_path / "docs" / "order.md").exists()


def test_publisher_rejects_duplicate_fragment_results_before_writing(
    tmp_path: Path,
) -> None:
    publisher = DocumentPublisher(tmp_path)
    result = DocumentResult("entry:save", ResultKind.UPSERT, "Save")

    with pytest.raises(ValueError, match="duplicate fragment"):
        publisher.publish(
            {"docs/order.md": (result, result)}
        )

    assert not (tmp_path / "docs" / "order.md").exists()


class RecordingLlm:
    def __init__(self, responses: tuple[str, ...] = (" Flow ", " Boundaries ")) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.calls.append((system_prompt, user_prompt))
        return self.responses[len(self.calls) - 1]


@pytest.fixture
def changed_project(
    tmp_path: Path,
) -> tuple[Path, FileTracker]:
    source_root = tmp_path / "src"
    source_root.mkdir()
    write_source(
        source_root,
        "frmOrder.frm",
        "Private Sub bSave_Click()\nCall ValidateOrder\nEnd Sub\n",
    )
    write_source(
        source_root,
        "modOrder.bas",
        "Public Sub ValidateOrder()\nresult = 1\nEnd Sub\n",
    )
    tracker = FileTracker(str(source_root))
    tracker.commit(message="initial source baseline")
    write_source(
        source_root,
        "modOrder.bas",
        "Public Sub ValidateOrder()\nresult = 2\nEnd Sub\n",
    )
    return source_root, tracker


@pytest.fixture
def change(changed_project: tuple[Path, FileTracker]) -> EntryChange:
    source_root, tracker = changed_project
    report = analyze_changes(
        tracker.scan(),
        load_vba_sources(source_root),
        [VbaAnalyzer()],
    )
    return entry_changes(report)[0]


def test_entry_generation_omits_history_on_creation_and_merges_in_order(
    change: EntryChange,
) -> None:
    creation = RecordingLlm()
    update = RecordingLlm()
    result = generate_entry_document(
        change,
        fragment_id="entry:save",
        existing_markdown=None,
        llm=creation,
    )
    generate_entry_document(
        change,
        fragment_id="entry:save",
        existing_markdown="## Save\n\nOld details.",
        llm=update,
    )

    assert result == DocumentResult("entry:save", ResultKind.UPSERT, "Flow\n\nBoundaries")
    assert [system for system, _ in creation.calls] == [
        "Extract the business flow.",
        "Identify boundaries and exceptions.",
    ]
    assert all("<ExistingFragment>" not in user for _, user in creation.calls)
    assert all("<ChangeSummary>" not in user for _, user in creation.calls)
    assert all("result = 2" in user for _, user in creation.calls)
    assert all("result = 1" not in user for _, user in creation.calls)
    assert all("<ExistingFragment>" in user for _, user in update.calls)
    assert all("Old details." in user for _, user in update.calls)
    assert all("<ChangeSummary>" in user for _, user in update.calls)
    assert all("result = 1" in user for _, user in update.calls)


def test_entry_generation_reads_caller_prompt_files(
    change: EntryChange, tmp_path: Path,
) -> None:
    write_source(tmp_path, "business_flow.system.txt", "First perspective")
    write_source(tmp_path, "boundaries.system.txt", "Second perspective")
    write_source(tmp_path, "entry.user.template", "{entry_id}\n{code}{history}")
    llm = RecordingLlm()

    generate_entry_document(
        change,
        fragment_id="custom:save",
        existing_markdown=None,
        llm=llm,
        prompt_directory=tmp_path,
    )

    assert [system for system, _ in llm.calls] == [
        "First perspective", "Second perspective",
    ]
    assert all(user.startswith(change.entry_id) for _, user in llm.calls)
    assert all("result = 2" in user for _, user in llm.calls)


@pytest.mark.parametrize(
    ("template", "error"),
    [(None, FileNotFoundError), (" ", ValueError), ("{unknown}", KeyError)],
)
def test_prompt_errors_propagate_before_calling_llm(
    change: EntryChange, tmp_path: Path, template: str | None, error: type[Exception],
) -> None:
    write_source(tmp_path, "business_flow.system.txt", "First")
    write_source(tmp_path, "boundaries.system.txt", "Second")
    if template is not None:
        write_source(tmp_path, "entry.user.template", template)
    llm = RecordingLlm()

    with pytest.raises(error):
        generate_entry_document(
            change,
            fragment_id="entry:save",
            existing_markdown=None,
            llm=llm,
            prompt_directory=tmp_path,
        )
    assert llm.calls == []


@pytest.mark.parametrize("responses", [("", "Valid"), ("Valid", " \n")])
def test_entry_generation_rejects_empty_completion(
    change: EntryChange, responses: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="empty LLM response"):
        generate_entry_document(
            change,
            fragment_id="entry:save",
            existing_markdown=None,
            llm=RecordingLlm(responses),
        )


def test_deleted_entry_needs_neither_prompt_files_nor_llm(
    change: EntryChange, tmp_path: Path,
) -> None:
    deleted = replace(change, new_entry=None, new_context=None)
    llm = RecordingLlm()

    result = generate_entry_document(
        deleted,
        fragment_id="entry:save",
        existing_markdown="Old content",
        llm=llm,
        prompt_directory=tmp_path / "missing",
    )

    assert result == DocumentResult("entry:save", ResultKind.DELETE)
    assert llm.calls == []
    assert entry_document_key(deleted) == "docs/frmOrder.md"


def test_missing_entry_context_is_an_explicit_error(change: EntryChange) -> None:
    llm = RecordingLlm()
    with pytest.raises(ValueError, match="missing new context"):
        generate_entry_document(
            replace(change, new_context=None),
            fragment_id="entry:save",
            existing_markdown=None,
            llm=llm,
        )
    assert llm.calls == []
    with pytest.raises(ValueError, match="missing from both contexts"):
        entry_document_key(replace(change, old_context=None, new_context=None))


def test_ui_generation_is_an_explicit_unimplemented_extension() -> None:
    with pytest.raises(NotImplementedError, match="UI component extraction"):
        generate_ui_document(SourceFile("form.frm", ""), fragment_id="ui:form")


def test_synchronize_publishes_llm_and_non_llm_results_together(
    changed_project: tuple[Path, FileTracker], tmp_path: Path,
) -> None:
    source_root, tracker = changed_project
    publisher = DocumentPublisher(tmp_path / "published")
    table = DocumentResult(
        "ui:frmOrder:components", ResultKind.UPSERT,
        "| Component | Type |\n| --- | --- |\n| bSave | Button |",
    )
    additional = {"docs/frmOrder.md": [table]}
    assert publisher.read_fragment("docs/frmOrder.md", table.fragment_id) is None

    synchronize(
        source_root, tracker, publisher, DemoLlmClient(),
        additional_results=additional,
    )

    assert additional == {"docs/frmOrder.md": [table]}
    assert publisher.read_fragment("docs/frmOrder.md", table.fragment_id) == table.markdown
    assert publisher.read_fragment(
        "docs/frmOrder.md", "entry:vba:frmOrder:bSave_Click",
    ) is not None
    report = analyze_changes(tracker.scan(), load_vba_sources(source_root), [VbaAnalyzer()])
    assert entry_changes(report) == ()


@pytest.mark.parametrize("failure_stage", ["generation", "publication"])
def test_failed_synchronization_preserves_baseline(
    changed_project: tuple[Path, FileTracker], tmp_path: Path, monkeypatch,
    failure_stage: str,
) -> None:
    source_root, tracker = changed_project
    before = tracker.scan()
    publisher = DocumentPublisher(tmp_path / "published")

    def fail(*args, **kwargs):
        raise RuntimeError("provider or storage unavailable")

    if failure_stage == "generation":
        monkeypatch.setattr(DemoLlmClient, "generate", fail)
    else:
        monkeypatch.setattr(DocumentPublisher, "publish", fail)

    with pytest.raises(RuntimeError, match="unavailable"):
        synchronize(source_root, tracker, publisher, DemoLlmClient())

    assert tracker.scan().baseline_revision == before.baseline_revision
    assert not publisher.root.exists()
