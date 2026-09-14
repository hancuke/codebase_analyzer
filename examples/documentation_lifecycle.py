# Example: one documentation synchronization flow for initial and later runs.
#
# An empty FileTracker baseline makes every discovered entry a CREATE plan.
# An existing baseline automatically produces UPDATE or ARCHIVE plans as
# appropriate; callers never classify forms themselves. Advancing the
# FileTracker baseline is intentionally owned by a separate caller workflow.

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from change_analyzer import ImpactReport, analyze_changes
from codegraph import SourceFile, VbaAnalyzer
from document_updater import (
    DocumentAction,
    DocumentSyncPlan,
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

from llm_client import (
    EntryAnalysisPrompt,
    MockLlmClient,
    MultiPromptEntryDocumentAuthor,
)


BEHAVIOR_ANALYSIS_PROMPT = """\
Analyze the entry point's externally observable behavior, control flow, and key dependencies.
Return concise findings for another technical writer; do not write the final document.

{{ reference_data }}
"""

CHANGE_ANALYSIS_PROMPT = """\
Analyze how the supplied changes affect this entry point and identify documentation risks,
including obsolete statements in the existing document.
Return concise findings for another technical writer; do not write the final document.

{{ reference_data }}
"""


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


def analyze_source_changes(
    source_root: Path,
    tracker: FileTracker,
) -> ImpactReport:
    """Build an impact report from the tracked source snapshot."""
    change_set = tracker.scan()
    return analyze_changes(
        change_set,
        sources(source_root),
        [VbaAnalyzer()],
    )


def load_document_state(
    plans: Iterable[EntryUpdatePlan],
    store: LocalDocumentStore,
    *,
    docs_root: str,
) -> tuple[tuple[SourceDocumentTarget, ...], dict[str, str]]:
    """Resolve source targets and load their current documents."""
    targets = resolve_document_targets(plans, docs_root=docs_root)
    return targets, store.load(targets)


def author_entry_documents(
    plans: Iterable[EntryUpdatePlan],
    targets: Iterable[SourceDocumentTarget],
    existing_documents: Mapping[str, str],
    *,
    content_for: Callable[[LlmEntryContext], str],
) -> tuple[EntryDocumentResult, ...]:
    """Generate one document result for each CREATE or UPDATE plan."""
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


def apply_document_sync_plan(
    store: LocalDocumentStore,
    sync_plan: DocumentSyncPlan,
) -> None:
    """Apply a validated document mutation plan."""
    store.apply(sync_plan)


def analyze_document_changes(
    source_root: Path,
    tracker: FileTracker,
) -> tuple[EntryUpdatePlan, ...]:
    """Return entry-scoped document plans for the current source changes."""
    report = analyze_source_changes(source_root, tracker)
    return create_entry_plans(report)


def print_plans(title: str, plans: Iterable[EntryUpdatePlan]) -> None:
    print(title)
    for plan in plans:
        print(f"  {plan.action.value:8} {plan.entry_id}")


with tempfile.TemporaryDirectory() as directory:
    project_root = Path(directory)
    source_root = project_root / "src"
    document_root = project_root / "published"
    source_root.mkdir()
    document_root.mkdir()
    write(
        source_root,
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
        source_root,
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
        str(source_root),
        exclude_patterns=[
            "docs",
            "**/docs/**",
            ".codegraph-reviews",
            "**/.codegraph-reviews/**",
        ],
    )
    store = LocalDocumentStore(document_root)

    llm_client = MockLlmClient()
    author = MultiPromptEntryDocumentAuthor(
        client=llm_client,
        analysis_prompts=(
            EntryAnalysisPrompt(
                name="behavior",
                template=BEHAVIOR_ANALYSIS_PROMPT,
            ),
            EntryAnalysisPrompt(
                name="change-impact",
                template=CHANGE_ANALYSIS_PROMPT,
            ),
        ),
    )

    plans = analyze_document_changes(source_root, tracker)
    targets, existing_documents = load_document_state(
        plans,
        store,
        docs_root="docs",
    )
    results = author_entry_documents(
        plans,
        targets,
        existing_documents,
        content_for=author.author,
    )
    sync_plan = build_document_sync_plan(
        plans,
        results,
        existing_documents,
        docs_root="docs",
    )
    apply_document_sync_plan(store, sync_plan)
    print_plans("DOCUMENT SYNCHRONIZATION (INITIAL)", plans)

    # Commit baseline and simulate an update
    tracker.commit()
    write(
        source_root,
        "modBusiness.bas",
        "Public Sub CheckPermission()\n"
        "    result = 2\n"
        "End Sub\n\n"
        "Public Sub SaveOrder()\n"
        "End Sub\n",
    )

    update_plans = analyze_document_changes(source_root, tracker)
    update_targets, existing_documents = load_document_state(
        update_plans,
        store,
        docs_root="docs",
    )
    update_results = author_entry_documents(
        update_plans,
        update_targets,
        existing_documents,
        content_for=author.author,
    )
    update_sync_plan = build_document_sync_plan(
        update_plans,
        update_results,
        existing_documents,
        docs_root="docs",
    )
    apply_document_sync_plan(store, update_sync_plan)
    print_plans("\nDOCUMENT SYNCHRONIZATION (UPDATE)", update_plans)

    print("\nSOURCE DOCUMENTS (FINAL)")
    for path in sorted((document_root / "docs").rglob("*.md")):
        print(f"  {path.relative_to(document_root)}")
        print(path.read_text(encoding="utf-8"))
