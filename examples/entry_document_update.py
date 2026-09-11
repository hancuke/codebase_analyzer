from __future__ import annotations

import tempfile
from pathlib import Path

from change_analyzer import analyze_changes, cluster_impacts
from codegraph import SourceFile, VbaAnalyzer
from document_updater import (
    DocumentAction,
    EntryDocumentResult,
    LocalDocumentStore,
    build_document_sync_plan,
    build_llm_context,
    create_entry_plans,
    extract_entry_document,
    resolve_document_targets,
)
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
        "End Sub\n\n"
        "Private Sub bCancel_Click()\n"
        "    Call CheckPermission\n"
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
    tracker = FileTracker(str(root))
    document_store = LocalDocumentStore(root)

    initial_report = analyze_changes(
        tracker.scan(), sources(root), [VbaAnalyzer()]
    )
    initial_plans = create_entry_plans(initial_report)
    initial_results = tuple(
        EntryDocumentResult(
            plan.entry_id,
            f"Initial documentation for `{plan.entry_id}`.",
        )
        for plan in initial_plans
        if plan.action in {DocumentAction.CREATE, DocumentAction.UPDATE}
    )
    initial_targets = resolve_document_targets(initial_plans)
    initial_sync = build_document_sync_plan(
        initial_plans,
        initial_results,
        document_store.load(initial_targets),
    )
    document_store.apply(initial_sync)
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
    plans = create_entry_plans(report)
    targets = resolve_document_targets(plans)
    existing_documents = document_store.load(targets)
    results = tuple(
        EntryDocumentResult(
            plan.entry_id,
            f"Updated documentation for `{plan.entry_id}`.",
        )
        for plan in plans
        if plan.action in {DocumentAction.CREATE, DocumentAction.UPDATE}
    )
    sync_plan = build_document_sync_plan(
        plans,
        results,
        existing_documents,
    )

    print("FUNCTION CHANGES")
    for change in report.function_changes:
        print(f"  {change.change_type.value:8} {change.function_id}")

    print("\nIMPACT BATCHES")
    for index, batch in enumerate(cluster_impacts(report), start=1):
        print(f"  Batch {index}")
        print(f"    entries: {', '.join(batch.entry_ids)}")
        print(f"    changes: {', '.join(batch.changed_function_ids)}")

    print("\nENTRY UPDATE PLANS")
    for plan in plans:
        changed = ", ".join(
            change.function_id for change in plan.function_changes
        )
        print(f"  {plan.action.value:8} {plan.entry_id}")
        print(f"           changes:  {changed}")

    print("\nSOURCE DOCUMENT MUTATIONS")
    for mutation in sync_plan.mutations:
        print(f"  {mutation.action.value:8} {mutation.target.document_path}")
    document_store.apply(sync_plan)

    print("\nSOURCE DOCUMENTS")
    for target in targets:
        document = root / target.document_path
        if document.exists():
            print(f"  {target.source_id} -> {target.document_path}")
            print(document.read_text(encoding="utf-8"))

    template_path = root / "update-document.md"
    template_path.write_text(
        "# Update the document\n\n"
        "Use the following reference data:\n\n"
        "{{ reference_data }}",
        encoding="utf-8",
    )

    print("\nONE RENDERED LLM PROMPT (truncated)")
    prompt_target = resolve_document_targets((plans[0],))[0]
    old_entry_document = extract_entry_document(
        existing_documents[prompt_target.document_path],
        source_id=prompt_target.source_id,
        entry_id=plans[0].entry_id,
    )
    reference_data = build_llm_context(
        plans[0],
        old_document=old_entry_document or "",
    ).to_reference_data_xml()
    prompt = template_path.read_text(encoding="utf-8").replace(
        "{{ reference_data }}", reference_data
    )
    print(prompt)
