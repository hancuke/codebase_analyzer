from __future__ import annotations

from pathlib import Path

from change_analyzer import analyze_changes
from codegraph import SourceFile, VbaAnalyzer
from document_updater import (
    DocumentCatalog,
    DocumentAction,
    DocumentRegistration,
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

    prompt = build_llm_context(
        plans[1],
        old_document="# Save button\n\nOld behavior.",
    ).to_prompt()
    assert "vba:frmOrder:bSave_Click" in prompt
    assert "vba:modRules:SharedRule" in prompt
    assert "Old behavior." in prompt
    assert "old_paths=" in prompt
    assert "new_paths=" in prompt


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
