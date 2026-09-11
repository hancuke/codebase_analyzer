from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from change_analyzer import analyze_changes
from codegraph import SourceFile, VbaAnalyzer
from document_updater import (
    DocumentAction,
    DocumentMutationAction,
    DocumentResultError,
    DocumentStateError,
    EntryDocumentResult,
    InvalidManagedDocumentError,
    LocalDocumentStore,
    build_document_sync_plan,
    create_entry_plans,
    extract_entry_document,
    resolve_document_targets,
)
from filetracker import FileTracker


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _sources(root: Path) -> tuple[SourceFile, ...]:
    return tuple(
        SourceFile(
            path.relative_to(root).as_posix(),
            path.read_text(encoding="utf-8"),
        )
        for path in sorted(root.rglob("*"))
        if path.suffix.casefold() in {".bas", ".frm"}
    )


def _analyze(root: Path, tracker: FileTracker):
    return analyze_changes(tracker.scan(), _sources(root), [VbaAnalyzer()])


def test_groups_entry_results_by_source_document(tmp_path: Path) -> None:
    tracker = FileTracker(str(tmp_path))
    _write(
        tmp_path,
        "forms/frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "End Sub\n\n"
        "Private Sub bCancel_Click()\n"
        "End Sub\n",
    )

    plans = create_entry_plans(_analyze(tmp_path, tracker))
    targets = resolve_document_targets(plans)
    sync_plan = build_document_sync_plan(
        plans,
        (
            EntryDocumentResult(
                "vba:frmOrder:bSave_Click",
                "Saves the current order.",
            ),
            EntryDocumentResult(
                "vba:frmOrder:bCancel_Click",
                "Cancels pending edits.",
            ),
        ),
        {},
    )

    assert targets[0].source_id == "forms/frmOrder.frm"
    assert targets[0].document_path == "docs/forms/frmOrder.md"
    assert len(sync_plan.mutations) == 1
    mutation = sync_plan.mutations[0]
    assert mutation.action is DocumentMutationAction.WRITE
    assert mutation.target == targets[0]
    assert mutation.content is not None
    assert mutation.content.index("bCancel_Click") < mutation.content.index(
        "bSave_Click"
    )
    assert mutation.content.count("codegraph:entry:start") == 2
    assert extract_entry_document(
        mutation.content,
        source_id="forms/frmOrder.frm",
        entry_id="vba:frmOrder:bSave_Click",
    ) == "## bSave_Click\n\nSaves the current order."


def test_updates_only_matching_entry_section(tmp_path: Path) -> None:
    tracker = FileTracker(str(tmp_path))
    _write(
        tmp_path,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "End Sub\n\n"
        "Private Sub bCancel_Click()\n"
        "End Sub\n",
    )
    create_plans = create_entry_plans(_analyze(tmp_path, tracker))
    initial_sync = build_document_sync_plan(
        create_plans,
        (
            EntryDocumentResult(
                "vba:frmOrder:bSave_Click",
                "Original save documentation.",
            ),
            EntryDocumentResult(
                "vba:frmOrder:bCancel_Click",
                "Original cancel documentation.",
            ),
        ),
        {},
    )
    store = LocalDocumentStore(tmp_path)
    store.apply(initial_sync)
    tracker.commit()

    _write(
        tmp_path,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "    result = 1\n"
        "End Sub\n\n"
        "Private Sub bCancel_Click()\n"
        "End Sub\n",
    )
    update_plans = create_entry_plans(_analyze(tmp_path, tracker))
    existing = store.load(resolve_document_targets(update_plans))
    update_sync = build_document_sync_plan(
        update_plans,
        (
            EntryDocumentResult(
                "vba:frmOrder:bSave_Click",
                "Updated save documentation.",
            ),
        ),
        existing,
    )

    assert len(update_sync.mutations) == 1
    content = update_sync.mutations[0].content
    assert content is not None
    assert "Updated save documentation." in content
    assert "Original save documentation." not in content
    assert "Original cancel documentation." in content


def test_archive_deletes_document_after_final_entry(tmp_path: Path) -> None:
    tracker = FileTracker(str(tmp_path))
    _write(
        tmp_path,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "End Sub\n",
    )
    create_plans = create_entry_plans(_analyze(tmp_path, tracker))
    create_sync = build_document_sync_plan(
        create_plans,
        (
            EntryDocumentResult(
                "vba:frmOrder:bSave_Click",
                "Save documentation.",
            ),
        ),
        {},
    )
    store = LocalDocumentStore(tmp_path)
    store.apply(create_sync)
    tracker.commit()
    (tmp_path / "frmOrder.frm").unlink()

    archive_plans = create_entry_plans(_analyze(tmp_path, tracker))
    existing = store.load(resolve_document_targets(archive_plans))
    archive_sync = build_document_sync_plan(archive_plans, (), existing)

    assert len(archive_sync.mutations) == 1
    assert archive_sync.mutations[0].action is DocumentMutationAction.DELETE
    store.apply(archive_sync)
    assert not (tmp_path / "docs/frmOrder.md").exists()


def test_archive_preserves_unmanaged_document_content(tmp_path: Path) -> None:
    tracker = FileTracker(str(tmp_path))
    _write(
        tmp_path,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "End Sub\n",
    )
    plan = create_entry_plans(_analyze(tmp_path, tracker))[0]
    created = build_document_sync_plan(
        (plan,),
        (
            EntryDocumentResult(
                plan.entry_id,
                "Save documentation.",
            ),
        ),
        {},
    ).mutations[0].content
    assert created is not None
    tracker.commit()
    (tmp_path / "frmOrder.frm").unlink()
    archive_plan = create_entry_plans(_analyze(tmp_path, tracker))[0]
    existing = {
        "docs/frmOrder.md": "Human-owned introduction.\n\n" + created,
    }

    sync_plan = build_document_sync_plan((archive_plan,), (), existing)

    assert sync_plan.mutations[0].action is DocumentMutationAction.WRITE
    assert sync_plan.mutations[0].content == (
        "Human-owned introduction.\n\n"
        '<!-- codegraph:source source_id="frmOrder.frm" -->\n'
        "# frmOrder.frm\n"
    )


def test_review_is_saved_without_publishing_document(tmp_path: Path) -> None:
    tracker = FileTracker(str(tmp_path))
    _write(
        tmp_path,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "End Sub\n",
    )
    create_plan = create_entry_plans(_analyze(tmp_path, tracker))[0]
    review_plan = replace(create_plan, action=DocumentAction.REVIEW)
    sync_plan = build_document_sync_plan(
        (review_plan,),
        (EntryDocumentResult(review_plan.entry_id, "Draft analysis."),),
        {},
    )

    assert sync_plan.mutations == ()
    assert sync_plan.pending_reviews[0].markdown == "Draft analysis."
    LocalDocumentStore(tmp_path).apply(sync_plan)
    review_files = tuple((tmp_path / ".codegraph-reviews").rglob("*.md"))
    assert len(review_files) == 1
    assert "Draft analysis." in review_files[0].read_text(encoding="utf-8")
    assert not (tmp_path / "docs/frmOrder.md").exists()


def test_rejects_missing_results_and_document_drift(tmp_path: Path) -> None:
    tracker = FileTracker(str(tmp_path))
    _write(
        tmp_path,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "End Sub\n",
    )
    create_plan = create_entry_plans(_analyze(tmp_path, tracker))[0]

    with pytest.raises(DocumentResultError, match="missing LLM results"):
        build_document_sync_plan((create_plan,), (), {})

    update_plan = replace(create_plan, action=DocumentAction.UPDATE)
    with pytest.raises(DocumentStateError, match="is absent"):
        build_document_sync_plan(
            (update_plan,),
            (EntryDocumentResult(update_plan.entry_id, "Updated."),),
            {},
        )


def test_rejects_malformed_and_injected_markers(tmp_path: Path) -> None:
    tracker = FileTracker(str(tmp_path))
    _write(
        tmp_path,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "End Sub\n",
    )
    plan = replace(
        create_entry_plans(_analyze(tmp_path, tracker))[0],
        action=DocumentAction.UPDATE,
    )
    path = "docs/frmOrder.md"

    with pytest.raises(DocumentResultError, match="reserved"):
        EntryDocumentResult(
            plan.entry_id,
            "<!-- codegraph:entry:start entry_id=\"other\" -->",
        )

    with pytest.raises(InvalidManagedDocumentError, match="no end marker"):
        build_document_sync_plan(
            (plan,),
            (EntryDocumentResult(plan.entry_id, "Updated."),),
            {
                path: (
                    '<!-- codegraph:entry:start '
                    f'entry_id="{plan.entry_id}" -->\n'
                )
            },
        )
