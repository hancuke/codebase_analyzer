# Example: one documentation synchronization flow for initial and later runs.
#
# An empty FileTracker baseline makes every discovered entry a CREATE plan.
# After a successful synchronization, the baseline is committed. Later runs
# compare against that baseline and automatically produce UPDATE or ARCHIVE
# plans as appropriate; callers never classify forms themselves.

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from change_analyzer import analyze_changes
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
        SourceFile(
            path.relative_to(root).as_posix(),
            path.read_text(encoding="utf-8"),
        )
        for path in sorted(root.rglob("*"))
        if path.suffix.casefold() in {".bas", ".frm"}
    )


def entry_source_id(plan: EntryUpdatePlan) -> str:
    """Return the source owning an entry from the context valid for its action."""
    contexts = (
        (plan.entry.old_context,)
        if plan.action is DocumentAction.ARCHIVE
        else (plan.entry.new_context, plan.entry.old_context)
    )
    for context in contexts:
        if context is not None:
            for function in context.functions:
                if function.id == plan.entry_id:
                    return function.source_id
    raise ValueError(f"entry {plan.entry_id!r} is missing from its context")


def author_entry_documents(
    plans: Iterable[EntryUpdatePlan],
    targets: Iterable[SourceDocumentTarget],
    existing_documents: Mapping[str, str],
    *,
    content_for: Callable[[LlmEntryContext], str],
) -> tuple[EntryDocumentResult, ...]:
    """Call the LLM once for each CREATE or UPDATE plan."""
    target_by_source = {target.source_id: target for target in targets}
    results: list[EntryDocumentResult] = []
    for plan in plans:
        if plan.action not in {DocumentAction.CREATE, DocumentAction.UPDATE}:
            continue
        target = target_by_source[entry_source_id(plan)]
        old_document = extract_entry_document(
            existing_documents.get(target.document_path, ""),
            source_id=target.source_id,
            entry_id=plan.entry_id,
        ) or ""
        results.append(
            EntryDocumentResult(
                plan.entry_id,
                content_for(build_llm_context(plan, old_document=old_document)),
            )
        )
    return tuple(results)


def synchronize_documents(
    root: Path,
    tracker: FileTracker,
    store: LocalDocumentStore,
    *,
    content_for: Callable[[LlmEntryContext], str],
) -> tuple[EntryUpdatePlan, ...]:
    """Synchronize documentation for both an empty and an existing baseline."""
    change_set = tracker.scan()
    report = analyze_changes(change_set, sources(root), [VbaAnalyzer()])
    plans = create_entry_plans(report)
    targets = resolve_document_targets(plans)
    existing_documents = store.load(targets)
    results = author_entry_documents(
        plans, targets, existing_documents, content_for=content_for
    )
    sync_plan = build_document_sync_plan(plans, results, existing_documents)
    store.apply(sync_plan)
    tracker.commit(
        message="documentation synchronized",
        expected_revision=change_set.working_revision,
        expected_baseline_revision=change_set.baseline_revision,
    )
    return plans


def print_plans(title: str, plans: Iterable[EntryUpdatePlan]) -> None:
    print(title)
    for plan in plans:
        print(f"  {plan.action.value:8} {plan.entry_id}")


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
        "modBusiness.bas",
        "Public Sub CheckPermission()\n"
        "    result = 1\n"
        "End Sub\n\n"
        "Public Sub SaveOrder()\n"
        "End Sub\n",
    )

    # Track source inputs only. Publishing docs must not invalidate the
    # revision captured immediately before this synchronization run.
    tracker = FileTracker(
        str(root),
        exclude_patterns=[
            "docs",
            "**/docs/**",
            ".codegraph-reviews",
            "**/.codegraph-reviews/**",
        ],
    )
    store = LocalDocumentStore(root)

    initial_plans = synchronize_documents(
        root,
        tracker,
        store,
        content_for=lambda context: (
            f"Initial documentation for `{context.plan.entry_id}`."
        ),
    )
    print_plans("INITIAL SYNCHRONIZATION", initial_plans)

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

    update_plans = synchronize_documents(
        root,
        tracker,
        store,
        content_for=lambda context: (
            f"Updated documentation for `{context.plan.entry_id}`."
        ),
    )
    print_plans("\nUPDATE SYNCHRONIZATION", update_plans)

    print("\nSOURCE DOCUMENTS")
    for path in sorted((root / "docs").rglob("*.md")):
        print(f"  {path.relative_to(root)}")
        print(path.read_text(encoding="utf-8"))
