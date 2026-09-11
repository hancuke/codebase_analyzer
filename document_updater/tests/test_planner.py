from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from xml.etree import ElementTree

from change_analyzer import analyze_changes
from codegraph import Diagnostic, SourceFile, VbaAnalyzer
from document_updater import (
    ArchivePromptReference,
    CreatePromptReference,
    DocumentAction,
    ReviewPromptReference,
    UpdatePromptReference,
    build_llm_context,
    create_entry_plans,
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


def test_plans_one_entry_update_per_affected_entry(tmp_path: Path) -> None:
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
    plans = create_entry_plans(report)

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
    reference_data = context.to_reference_data_xml()
    root = ElementTree.fromstring(reference_data)

    assert root.tag == "reference_data"
    assert root.findtext("action") == "update"
    assert root.find("instructions") is None
    assert root.findtext("existing_document") == (
        "# Save <button>\n\nOld & correct behavior."
    )
    assert "vba:frmOrder:bSave_Click" in reference_data
    assert "vba:modRules:SharedRule" in reference_data
    assert "&lt;button&gt;" in reference_data
    assert "Old &amp; correct behavior." in reference_data
    assert root.find("entry/impact_evidence/change/old_paths") is not None
    assert root.find("entry/impact_evidence/change/new_paths") is not None
    assert root.find("document_path") is None
    assert root.find("covered_entry_ids") is None
    assert root.find(".//source_range") is None
    assert root.find("diagnostics") is None
    assert context.to_reference_data_xml() == reference_data
    assert payload.to_reference_data_xml() == reference_data
    assert ElementTree.fromstring(
        build_llm_context(plans[1]).to_reference_data_xml()
    ).find("existing_document") is None
    prompt = "# Update the document\n\n{{ reference_data }}".replace(
        "{{ reference_data }}", reference_data
    )
    assert reference_data in prompt


def test_empty_baseline_creates_entry_plans_for_new_entries(tmp_path: Path) -> None:
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
    plans = create_entry_plans(report)

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
    assert plans[0].entry.old_context is None
    assert plans[0].entry.new_context is not None

    context = build_llm_context(
        plans[0],
        old_document="This must not be included.",
    )
    payload = context.to_payload()
    assert isinstance(payload.reference_data, CreatePromptReference)
    reference_data = context.to_reference_data_xml()
    root = ElementTree.fromstring(reference_data)

    assert root.findtext("action") == "create"
    assert root.find("entry/new_context") is not None
    assert root.find("existing_document") is None
    assert root.find("entry/old_context") is None
    assert root.find("entry/impact_evidence") is None
    assert root.find("function_changes") is None
    assert root.find("call_edge_changes") is None
    assert root.find("diagnostics") is None
    assert "This must not be included." not in reference_data


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
    plans = create_entry_plans(report)

    assert len(plans) == 1
    assert plans[0].action is DocumentAction.ARCHIVE
    context = build_llm_context(plans[0], old_document="# Old order document")
    payload = context.to_payload()
    assert isinstance(payload.reference_data, ArchivePromptReference)
    assert all(
        change.change_type == "deleted"
        for change in payload.reference_data.deleted_functions
    )
    root = ElementTree.fromstring(context.to_reference_data_xml())

    assert root.findtext("action") == "archive"
    assert root.find("entry/old_context") is not None
    assert root.find("entry/new_context") is None
    assert root.find("entry/impact_evidence/change/old_paths") is not None
    assert root.find("entry/impact_evidence/change/new_paths") is None
    assert root.find("deleted_functions") is not None
    assert root.find("function_changes") is None
    assert root.find("diagnostics") is None
    assert ElementTree.fromstring(
        build_llm_context(plans[0]).to_reference_data_xml()
    ).find("existing_document") is None


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
        create_entry_plans(report)[0],
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
    reference_data = context.to_reference_data_xml()
    root = ElementTree.fromstring(reference_data)

    assert root.findtext("action") == "review"
    assert root.find("existing_document") is None
    assert root.find("entry/new_context") is not None
    assert root.find("function_changes") is not None
    assert root.find("diagnostics") is None
    assert "ambiguous_call" not in reference_data


def test_plans_are_not_grouped_when_entries_share_a_source_file(tmp_path: Path) -> None:
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
    plans = create_entry_plans(report)

    assert [plan.entry_id for plan in plans] == [
        "vba:frmCustomer:bDelete_Click",
        "vba:frmOrder:bCancel_Click",
        "vba:frmOrder:bSave_Click",
    ]
    assert all(plan.entry.impact.entry_id == plan.entry_id for plan in plans)
    assert all(
        [change.function_id for change in plan.function_changes]
        == ["vba:modRules:SharedRule"]
        for plan in plans
    )
