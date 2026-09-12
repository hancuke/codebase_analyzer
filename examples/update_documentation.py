# Example: updating documentation after a code change.
# Self-contained: establishes a baseline, then mutates code and re-docs.

from __future__ import annotations

import tempfile
from pathlib import Path
from collections.abc import Callable, Iterable, Mapping

from change_analyzer import analyze_changes, cluster_impacts
from codegraph import SourceFile, VbaAnalyzer
from document_updater import (
    DocumentAction,
    EntryDocumentResult,
    LocalDocumentStore,
    SourceDocumentTarget,
    build_document_sync_plan,
    build_llm_context,
    create_entry_plans,
    extract_entry_document,
    resolve_document_targets,
)
from document_updater.models import EntryUpdatePlan, LlmEntryContext

from filetracker import FileTracker


def write(root: Path, name: str, content: str) -> None:
    (root / name).write_text(content, encoding="utf-8")


def sources(root: Path) -> tuple[SourceFile, ...]:
    return tuple(
        SourceFile(path.name, path.read_text(encoding="utf-8"))
        for path in sorted(root.iterdir())
        if path.suffix.casefold() in {".bas", ".frm"}
    )


def _entry_source_id(plan: EntryUpdatePlan) -> str:
    """入口所属源文件（source_id）。

    与 resolve_document_targets 内部用的规则一致：在依赖上下文里找到与本入口
    同 id 的函数，取其 source_id。CREATE/UPDATE 看新图上下文，ARCHIVE 看旧图。
    """
    if plan.action is DocumentAction.ARCHIVE:
        context = plan.entry.old_context
    else:
        context = plan.entry.new_context or plan.entry.old_context
    assert context is not None, f"entry {plan.entry_id!r} missing context"
    for function in context.functions:
        if function.id == plan.entry_id:
            return function.source_id
    raise AssertionError(f"entry {plan.entry_id!r} not found in its context")


def author_entry_documents(
    plans: Iterable[EntryUpdatePlan],
    targets: Iterable[SourceDocumentTarget],
    existing_documents: Mapping[str, str],
    *,
    content_for: Callable[[LlmEntryContext], str],
) -> tuple[EntryDocumentResult, ...]:
    """为每个需要落库的入口产出文档正文（EntryDocumentResult）。

    这正是生产环境里“调用 LLM 写文档”的位置，语义是：

    1. build_llm_context(plan, old_document=...) 把该入口的参考材料
       （依赖上下文、变更证据、已有文档）打包成 LlmEntryContext；
    2. 把参考材料序列化后交给模型，模型回复即成为该入口的 markdown；
    3. 用 EntryDocumentResult 收口，交给 build_document_sync_plan 落盘。

    content_for 在这里扮演“模型”的角色：demo 不依赖真实 LLM，
    只根据参考材料生成占位文本。真实接入时，应改为
    `content_for = lambda ctx: call_my_llm(ctx.to_reference_data_xml())`。
    """
    document_path_by_source = {
        target.source_id: target.document_path for target in targets
    }
    results: list[EntryDocumentResult] = []
    for plan in plans:
        if plan.action not in {DocumentAction.CREATE, DocumentAction.UPDATE}:
            continue
        source_id = _entry_source_id(plan)
        document_path = document_path_by_source[source_id]
        old_document = extract_entry_document(
            existing_documents.get(document_path, ""),
            source_id=source_id,
            entry_id=plan.entry_id,
        ) or ""
        context = build_llm_context(plan, old_document=old_document)
        results.append(EntryDocumentResult(plan.entry_id, content_for(context)))
    return tuple(results)

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
    # LocalDocumentStore 把文档写到 <root>/docs/ 下（docs_root 默认 "docs"）。
    document_store = LocalDocumentStore(root)

    baseline_report = analyze_changes(
        tracker.scan(), sources(root), [VbaAnalyzer()]
    )
    baseline_plans = create_entry_plans(baseline_report)
    baseline_targets = resolve_document_targets(baseline_plans)
    # 首次运行没有已有文档，existing_documents 为空。
    existing_documents = document_store.load(baseline_targets)
    baseline_results = author_entry_documents(
        baseline_plans,
        baseline_targets,
        existing_documents,
        content_for=lambda ctx: f"Initial documentation for `{ctx.plan.entry_id}`.",
    )
    baseline_sync_plan = build_document_sync_plan(
        baseline_plans,
        baseline_results,
        existing_documents,
    )
    # 持久化：result 变成 WRITE mutation，合并进 docs/<source>.md，由 store 原子写入。
    document_store.apply(baseline_sync_plan)
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
    change_report = analyze_changes(change_set, sources(root), [VbaAnalyzer()])
    change_plans = create_entry_plans(change_report)
    document_targets = resolve_document_targets(change_plans)
    existing_documents = document_store.load(document_targets)
    change_results = author_entry_documents(
        change_plans,
        document_targets,
        existing_documents,
        content_for=lambda ctx: f"Updated documentation for `{ctx.plan.entry_id}`.",
    )
    
    print("FUNCTION CHANGES")
    for change in change_report.function_changes:
        print(f"  {change.change_type.value:8} {change.function_id}")

    print("\nIMPACT BATCHES")
    for index, batch in enumerate(cluster_impacts(change_report), start=1):
        print(f"  Batch {index}")
        print(f"    entries: {', '.join(batch.entry_ids)}")
        print(f"    changes: {', '.join(batch.changed_function_ids)}")

    print("\nENTRY UPDATE PLANS")
    for plan in change_plans:
        changed = ", ".join(
            change.function_id for change in plan.function_changes
        )
        print(f"  {plan.action.value:8} {plan.entry_id}")
        print(f"           changes:  {changed}")

    document_sync_plan = build_document_sync_plan(
            change_plans,
            change_results,
            existing_documents,
        )
    
    print("\nSOURCE DOCUMENT MUTATIONS")
    for mutation in document_sync_plan.mutations:
        print(f"  {mutation.action.value:8} {mutation.target.document_path}")
    # 持久化：result 变成 WRITE mutation，合并进 docs/<source>.md，由 store 原子写入。
    document_store.apply(document_sync_plan)

    print("\nSOURCE DOCUMENTS")
    for target in document_targets:
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

    print("\nREFERENCE DATA FED TO THE LLM (first entry, truncated)")
    # 这正是 author_entry_documents 内部为 change_plans[0] 调用 build_llm_context
    # 时构造、并交给 content_for（即“模型”）的同一份参考材料。
    first_plan_target = resolve_document_targets((change_plans[0],))[0]
    existing_entry_doc = extract_entry_document(
        existing_documents[first_plan_target.document_path],
        source_id=first_plan_target.source_id,
        entry_id=change_plans[0].entry_id,
    )
    reference_data = build_llm_context(
        change_plans[0],
        old_document=existing_entry_doc or "",
    ).to_reference_data_xml()
    prompt = template_path.read_text(encoding="utf-8").replace(
        "{{ reference_data }}", reference_data
    )
    print(prompt)
