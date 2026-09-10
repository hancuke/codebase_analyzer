from __future__ import annotations

import tempfile
from pathlib import Path

from change_analyzer import analyze_changes, cluster_impacts
from codegraph import SourceFile, VbaAnalyzer
from document_updater import DocumentCatalog, build_llm_context, create_document_plans
from filetracker import FileTracker


def write(root: Path, name: str, content: str) -> None:
    (root / name).write_text(content, encoding="utf-8")


def sources(root: Path) -> tuple[SourceFile, ...]:
    return tuple(
        SourceFile(path.name, path.read_text(encoding="utf-8"))
        for path in sorted(root.iterdir())
        if path.suffix.casefold() in {".bas", ".frm"}
    )


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)

    write(
        root,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "    Call CheckPermission\n"
        "    Call SaveOrder\n"
        "End Sub\n",
    )
    write(
        root,
        "frmCustomer.frm",
        "Private Sub bDelete_Click()\n"
        "    Call CheckPermission\n"
        "End Sub\n",
    )
    write(
        root,
        "modBusiness.bas",
        "Public Sub CheckPermission()\n"
        "    result = 1\n"
        "End Sub\n\n"
        "Public Sub SaveOrder()\n"
        "End Sub\n",
    )
    write(
        root,
        "frmReport.frm",
        "Private Sub bRefresh_Click()\n"
        "    Call RefreshReport\n"
        "End Sub\n",
    )
    write(
        root,
        "modReport.bas",
        "Public Sub RefreshReport()\n"
        "    result = 1\n"
        "End Sub\n",
    )
    registry_path = root / "document-registry.json"
    registry_path.write_text(
        """{
  "version": 1,
  "documents": [
    {
      "path": "docs/forms/order.md",
      "entry_ids": ["vba:frmOrder:bSave_Click"]
    },
    {
      "path": "docs/forms/customer.md",
      "entry_ids": ["vba:frmCustomer:bDelete_Click"]
    },
    {
      "path": "docs/forms/report.md",
      "entry_ids": ["vba:frmReport:bRefresh_Click"]
    }
  ]
}
""",
        encoding="utf-8",
    )

    tracker = FileTracker(str(root))
    tracker.commit(message="initial code")

    write(
        root,
        "modBusiness.bas",
        "Public Sub CheckPermission()\n"
        "    result = 2\n"
        "End Sub\n\n"
        "Public Sub SaveOrder()\n"
        "    Call AuditOrder\n"
        "End Sub\n\n"
        "Public Sub AuditOrder()\n"
        "End Sub\n",
    )
    write(
        root,
        "modReport.bas",
        "Public Sub RefreshReport()\n"
        "    result = 2\n"
        "End Sub\n",
    )

    change_set = tracker.scan()
    report = analyze_changes(change_set, sources(root), [VbaAnalyzer()])
    plans = create_document_plans(report, DocumentCatalog.load(registry_path))

    print("FUNCTION CHANGES")
    for change in report.function_changes:
        print(f"  {change.change_type.value:8} {change.function_id}")

    print("\nIMPACT BATCHES")
    for index, batch in enumerate(cluster_impacts(report), start=1):
        print(f"  Batch {index}")
        print(f"    entries: {', '.join(batch.entry_ids)}")
        print(f"    changes: {', '.join(batch.changed_function_ids)}")

    print("\nDOCUMENT UPDATE PLANS")
    for plan in plans:
        changed = ", ".join(
            change.function_id for change in plan.function_changes
        )
        print(f"  {plan.action.value:8} {plan.document_path}")
        print(f"           entries:  {', '.join(plan.entry_ids)}")
        print(f"           changes:  {changed}")

    print("\nONE LLM-READY CONTEXT (truncated)")
    prompt = build_llm_context(
        plans[0],
        old_document="# Existing document\n\nDescribe the current entry behavior.",
    ).to_prompt()
    print(prompt[:1600])
