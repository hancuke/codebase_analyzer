from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

from change_analyzer import analyze_changes
from codegraph import Diagnostic, SourceFile, VbaAnalyzer
from document_updater import (
    ArchivePromptReference,
    CreatePromptReference,
    DocumentCatalog,
    DocumentAction,
    DocumentRegistration,
    ReviewPromptReference,
    UpdatePromptReference,
    build_llm_context,
    create_document_plans,
)
from filetracker import FileTracker


def _write(root: Path, name: str, content: str) -> None:
    (root / name).write_text(content, encoding="utf-8")


def _sources(root: Path) -> tuple[SourceFile, ...]:
    return tuple(
        SourceFile(path.name, path.read_text(encoding="utf-8"))
        for path in sorted(root.glob("*"))
        if path.suffix.casefold() in {".bas", ".frm"}
    )


def test_plans_one_document_update_per_affected_entry(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "    Call SharedRule\n"
        "End Sub\n",
    )
    _write(
        tmp_path,
        "frmCustomer.frm",
        "Private Sub bDelete_Click()\n"
        "    Call SharedRule\n"
        "End Sub\n",
    )
    _write(
        tmp_path,
        "modRules.bas",
        "Public Sub SharedRule()\n"
        "    result = 1\n"
        "End Sub\n",
    )
    tracker = FileTracker(str(tmp_path))
    tracker.commit()
    _write(
        tmp_path,
        "modRules.bas",
        "Public Sub SharedRule()\n"
        "    result = 2\n"
        "End Sub\n",
    )

    report = analyze_changes(tracker.scan(), _sources(tmp_path), [VbaAnalyzer()])
    plans = create_document_plans(report)

    assert [plan.entry_id for plan in plans] == [
        "vba:frmCustomer:bDelete_Click",
        "vba:frmOrder:bSave_Click",
    ]
    assert all(plan.action is DocumentAction.UPDATE for plan in plans)
    assert all(
        [change.function_id for change in plan.function_changes]
        == ["vba:modRules:SharedRule"]
        for plan in plans
    )

    context = build_llm_context(
        plans[1],
        old_document="# Save <button>\n\nOld & correct behavior.",
    )
    payload = context.to_payload()
    assert isinstance(payload.reference_data, UpdatePromptReference)
    prompt = context.to_prompt()
    root = ElementTree.fromstring(prompt)

    assert root.findtext("reference_data/action") == "update"
    assert root.findtext("instructions/objective") == (
        "Update the entry-oriented code document."
    )
    assert root.findtext("reference_data/existing_document") == (
        "# Save <button>\n\nOld & correct behavior."
    )
    assert "vba:frmOrder:bSave_Click" in prompt
    assert "vba:modRules:SharedRule" in prompt
    assert "&lt;button&gt;" in prompt
    assert "Old &amp; correct behavior." in prompt
    assert root.find("reference_data/entries/entry/impact_evidence/"
                     "change/old_paths") is not None
    assert root.find("reference_data/entries/entry/impact_evidence/"
                     "change/new_paths") is not None
    assert root.find(".//source_range") is None
    assert root.find("reference_data/diagnostics") is None
    assert context.to_prompt() == prompt


def test_empty_baseline_creates_documents_for_new_entries(tmp_path: Path) -> None:
    tracker = FileTracker(str(tmp_path))
    _write(
        tmp_path,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "    Call SaveOrder\n"
        "End Sub\n",
    )
    _write(
        tmp_path,
        "modOrder.bas",
        "Public Sub SaveOrder()\n"
        "End Sub\n",
    )

    report = analyze_changes(tracker.scan(), _sources(tmp_path), [VbaAnalyzer()])
    plans = create_document_plans(report)

    assert report.old_codebase.functions == ()
    assert [function.id for function in report.new_codebase.functions] == [
        "vba:frmOrder:bSave_Click",
        "vba:modOrder:SaveOrder",
    ]
    assert len(plans) == 1
    assert plans[0].entry_id == "vba:frmOrder:bSave_Click"
    assert plans[0].action is DocumentAction.CREATE
    assert {
        change.function_id for change in plans[0].function_changes
    } == {
        "vba:frmOrder:bSave_Click",
        "vba:modOrder:SaveOrder",
    }
    assert plans[0].entries[0].old_context is None
    assert plans[0].entries[0].new_context is not None

    context = build_llm_context(
        plans[0],
        old_document="This must not be included.",
    )
    payload = context.to_payload()
    assert isinstance(payload.reference_data, CreatePromptReference)
    root = ElementTree.fromstring(context.to_prompt())

    assert root.findtext("reference_data/action") == "create"
    assert root.find("reference_data/entries/entry/new_context") is not None
    assert root.find("reference_data/existing_document") is None
    assert root.find("reference_data/entries/entry/old_context") is None
    assert root.find("reference_data/entries/entry/impact_evidence") is None
    assert root.find("reference_data/function_changes") is None
    assert root.find("reference_data/call_edge_changes") is None
    assert root.find("reference_data/diagnostics") is None
    assert "This must not be included." not in context.to_prompt()


def test_archive_prompt_only_contains_baseline_and_deletion_facts(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "    Call SaveOrder\n"
        "End Sub\n",
    )
    _write(
        tmp_path,
        "modOrder.bas",
        "Public Sub SaveOrder()\n"
        "End Sub\n",
    )
    tracker = FileTracker(str(tmp_path))
    tracker.commit()
    (tmp_path / "frmOrder.frm").unlink()
    (tmp_path / "modOrder.bas").unlink()

    report = analyze_changes(tracker.scan(), _sources(tmp_path), [VbaAnalyzer()])
    plans = create_document_plans(report)

    assert len(plans) == 1
    assert plans[0].action is DocumentAction.ARCHIVE
    context = build_llm_context(plans[0], old_document="# Old order document")
    payload = context.to_payload()
    assert isinstance(payload.reference_data, ArchivePromptReference)
    assert all(
        change.change_type == "deleted"
        for change in payload.reference_data.deleted_functions
    )
    root = ElementTree.fromstring(context.to_prompt())

    assert root.findtext("reference_data/action") == "archive"
    assert root.find("reference_data/entries/entry/old_context") is not None
    assert root.find("reference_data/entries/entry/new_context") is None
    assert root.find("reference_data/entries/entry/impact_evidence/"
                     "change/old_paths") is not None
    assert root.find("reference_data/entries/entry/impact_evidence/"
                     "change/new_paths") is None
    assert root.find("reference_data/deleted_functions") is not None
    assert root.find("reference_data/function_changes") is None
    assert root.find("reference_data/diagnostics") is None


def test_review_prompt_keeps_available_facts_but_omits_diagnostics(
    tmp_path: Path,
) -> None:
    tracker = FileTracker(str(tmp_path))
    _write(
        tmp_path,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "End Sub\n",
    )
    report = analyze_changes(tracker.scan(), _sources(tmp_path), [VbaAnalyzer()])
    plan = replace(
        create_document_plans(report)[0],
        action=DocumentAction.REVIEW,
        diagnostics=(
            Diagnostic(
                code="ambiguous_call",
                severity="error",
                message="Do not expose this diagnostic.",
            ),
        ),
    )

    context = build_llm_context(plan)
    payload = context.to_payload()
    assert isinstance(payload.reference_data, ReviewPromptReference)
    root = ElementTree.fromstring(context.to_prompt())

    assert root.findtext("reference_data/action") == "review"
    assert root.find("reference_data/existing_document") is None
    assert root.find("reference_data/entries/entry/new_context") is not None
    assert root.find("reference_data/function_changes") is not None
    assert root.find("reference_data/diagnostics") is None
    assert "ambiguous_call" not in context.to_prompt()


def test_catalog_groups_affected_form_entries_into_one_document(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "    Call SharedRule\n"
        "End Sub\n\n"
        "Private Sub bCancel_Click()\n"
        "    Call SharedRule\n"
        "End Sub\n",
    )
    _write(
        tmp_path,
        "frmCustomer.frm",
        "Private Sub bDelete_Click()\n"
        "    Call SharedRule\n"
        "End Sub\n",
    )
    _write(
        tmp_path,
        "modRules.bas",
        "Public Sub SharedRule()\n"
        "    result = 1\n"
        "End Sub\n",
    )
    tracker = FileTracker(str(tmp_path))
    tracker.commit()
    _write(
        tmp_path,
        "modRules.bas",
        "Public Sub SharedRule()\n"
        "    result = 2\n"
        "End Sub\n",
    )

    report = analyze_changes(tracker.scan(), _sources(tmp_path), [VbaAnalyzer()])
    catalog = DocumentCatalog(
        (
            DocumentRegistration(
                "docs/forms/order.md",
                (
                    "vba:frmOrder:bSave_Click",
                    "vba:frmOrder:bCancel_Click",
                ),
            ),
            DocumentRegistration(
                "docs/forms/customer.md",
                ("vba:frmCustomer:bDelete_Click",),
            ),
        )
    )

    plans = create_document_plans(report, catalog)

    assert [plan.document_path for plan in plans] == [
        "docs/forms/customer.md",
        "docs/forms/order.md",
    ]
    order_plan = plans[1]
    assert order_plan.entry_ids == (
        "vba:frmOrder:bCancel_Click",
        "vba:frmOrder:bSave_Click",
    )
    assert order_plan.covered_entry_ids == order_plan.entry_ids
    assert [change.function_id for change in order_plan.function_changes] == [
        "vba:modRules:SharedRule"
    ]
