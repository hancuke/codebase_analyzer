from __future__ import annotations

import difflib
from collections import defaultdict
from collections.abc import Sequence

from codegraph import AnalysisContext, Codebase, Diagnostic, LanguageAnalyzer, SourceFile
from filetracker import ChangeSet

from .models import (
    CallEdgeChange,
    ChangeType,
    EntryImpact,
    EntryImpactEvidence,
    EntryChange,
    FunctionChange,
    ImpactBatch,
    ImpactReport,
)
from .snapshots import build_source_snapshots


def analyze_changes(
    change_set: ChangeSet,
    working_sources: Sequence[SourceFile],
    analyzers: Sequence[LanguageAnalyzer],
) -> ImpactReport:
    snapshots = build_source_snapshots(change_set, tuple(working_sources))
    old_codebase = Codebase.analyze(snapshots.baseline, analyzers)
    new_codebase = Codebase.analyze(snapshots.working, analyzers)
    function_changes = _compare_functions(old_codebase, new_codebase)
    call_edge_changes = _compare_call_edges(old_codebase, new_codebase)
    entry_impacts, unassigned = _find_entry_impacts(
        old_codebase,
        new_codebase,
        function_changes,
    )
    return ImpactReport(
        baseline_revision=change_set.baseline_revision,
        working_revision=change_set.working_revision,
        old_codebase=old_codebase,
        new_codebase=new_codebase,
        function_changes=function_changes,
        call_edge_changes=call_edge_changes,
        entry_impacts=entry_impacts,
        unassigned_changes=unassigned,
        snapshot_issues=snapshots.issues,
    )


def cluster_impacts(report: ImpactReport) -> tuple[ImpactBatch, ...]:
    """Group related entries and changed functions into connected components."""

    entry_to_functions = {
        impact.entry_id: {
            evidence.changed_function_id for evidence in impact.evidence
        }
        for impact in report.entry_impacts
    }
    function_to_entries: dict[str, set[str]] = defaultdict(set)
    for entry_id, function_ids in entry_to_functions.items():
        for function_id in function_ids:
            function_to_entries[function_id].add(entry_id)

    pending = set(entry_to_functions)
    batches: list[ImpactBatch] = []
    while pending:
        entry_queue = [min(pending)]
        component_entries: set[str] = set()
        component_functions: set[str] = set()
        while entry_queue:
            entry_id = entry_queue.pop()
            if entry_id in component_entries:
                continue
            component_entries.add(entry_id)
            pending.discard(entry_id)
            for function_id in entry_to_functions[entry_id]:
                if function_id in component_functions:
                    continue
                component_functions.add(function_id)
                entry_queue.extend(function_to_entries[function_id] - component_entries)
        batches.append(
            ImpactBatch(
                entry_ids=tuple(sorted(component_entries)),
                changed_function_ids=tuple(sorted(component_functions)),
            )
        )
    return tuple(
        sorted(
            batches,
            key=lambda batch: (batch.entry_ids, batch.changed_function_ids),
        )
    )


def entry_changes(report: ImpactReport) -> tuple[EntryChange, ...]:
    """Project deterministic, entry-scoped code facts from an impact report."""
    changes_by_id = {
        change.function_id: change for change in report.function_changes
    }
    projected: list[EntryChange] = []
    for impact in report.entry_impacts:
        old_context = _context_or_none(report.old_codebase, impact.entry_id)
        new_context = _context_or_none(report.new_codebase, impact.entry_id)
        context_ids = {
            function.id
            for context in (old_context, new_context)
            if context is not None
            for function in context.functions
        }
        projected.append(
            EntryChange(
                entry_id=impact.entry_id,
                old_entry=impact.old_entry,
                new_entry=impact.new_entry,
                old_context=old_context,
                new_context=new_context,
                evidence=impact.evidence,
                function_changes=tuple(
                    changes_by_id[item.changed_function_id]
                    for item in impact.evidence
                ),
                call_edge_changes=_entry_call_edge_changes(
                    report.call_edge_changes,
                    context_ids,
                ),
                diagnostics=_entry_diagnostics(old_context, new_context),
            )
        )
    return tuple(projected)


def _context_or_none(
    codebase: Codebase,
    entry_id: str,
) -> AnalysisContext | None:
    if codebase.get_function(entry_id) is None:
        return None
    return codebase.dependency_context(entry_id)


def _entry_call_edge_changes(
    changes: tuple[CallEdgeChange, ...],
    context_ids: set[str],
) -> tuple[CallEdgeChange, ...]:
    unique = {
        (
            change.source_function_id,
            change.target_function_id,
            change.change_type.value,
        ): change
        for change in changes
        if change.source_function_id in context_ids
        or change.target_function_id in context_ids
    }
    return tuple(unique[key] for key in sorted(unique))


def _entry_diagnostics(
    old_context: AnalysisContext | None,
    new_context: AnalysisContext | None,
) -> tuple[Diagnostic, ...]:
    unique = {
        (
            item.code,
            item.severity,
            item.message,
            item.source_id,
            item.function_id,
            item.line,
        ): item
        for context in (old_context, new_context)
        if context is not None
        for item in context.diagnostics
    }
    return tuple(
        unique[key]
        for key in sorted(
            unique,
            key=lambda item: (
                item[3] or "",
                item[5] or 0,
                item[0],
                item[4] or "",
            ),
        )
    )


def _compare_functions(
    old_codebase: Codebase,
    new_codebase: Codebase,
) -> tuple[FunctionChange, ...]:
    old_functions = {function.id: function for function in old_codebase.functions}
    new_functions = {function.id: function for function in new_codebase.functions}
    changes: list[FunctionChange] = []

    for function_id in sorted(set(old_functions) | set(new_functions)):
        old_function = old_functions.get(function_id)
        new_function = new_functions.get(function_id)
        if old_function is None:
            change_type = ChangeType.ADDED
        elif new_function is None:
            change_type = ChangeType.DELETED
        elif old_function.source != new_function.source:
            change_type = ChangeType.MODIFIED
        else:
            continue
        changes.append(
            FunctionChange(
                function_id=function_id,
                change_type=change_type,
                old_function=old_function,
                new_function=new_function,
                diff=_function_diff(old_function, new_function),
            )
        )
    return tuple(changes)


def _function_diff(old_function, new_function) -> str:
    old_source = old_function.source if old_function is not None else ""
    new_source = new_function.source if new_function is not None else ""
    source_id = (
        new_function.source_id if new_function is not None else old_function.source_id
    )
    return "".join(
        difflib.unified_diff(
            old_source.splitlines(keepends=True),
            new_source.splitlines(keepends=True),
            fromfile=f"{source_id}:baseline",
            tofile=f"{source_id}:working",
        )
    )


def _compare_call_edges(
    old_codebase: Codebase,
    new_codebase: Codebase,
) -> tuple[CallEdgeChange, ...]:
    old_edges = _resolved_edges(old_codebase)
    new_edges = _resolved_edges(new_codebase)
    return tuple(
        [
            *(
                CallEdgeChange(source, target, ChangeType.DELETED)
                for source, target in sorted(old_edges - new_edges)
            ),
            *(
                CallEdgeChange(source, target, ChangeType.ADDED)
                for source, target in sorted(new_edges - old_edges)
            ),
        ]
    )


def _resolved_edges(codebase: Codebase) -> set[tuple[str, str]]:
    return {
        (function.id, call.target_id)
        for function in codebase.functions
        for call in codebase.calls_from(function.id, resolution="resolved")
        if call.target_id is not None
    }


def _find_entry_impacts(
    old_codebase: Codebase,
    new_codebase: Codebase,
    function_changes: tuple[FunctionChange, ...],
) -> tuple[tuple[EntryImpact, ...], tuple[FunctionChange, ...]]:
    old_entries = {entry.function_id: entry for entry in old_codebase.entry_points}
    new_entries = {entry.function_id: entry for entry in new_codebase.entry_points}
    evidence_by_entry: dict[str, list[EntryImpactEvidence]] = defaultdict(list)
    unassigned: list[FunctionChange] = []

    for change in function_changes:
        old_affected = _affected_entries(
            old_codebase, old_entries, change.function_id
        )
        new_affected = _affected_entries(
            new_codebase, new_entries, change.function_id
        )
        affected = old_affected | new_affected
        if not affected:
            unassigned.append(change)
            continue

        for entry_id in sorted(affected):
            evidence_by_entry[entry_id].append(
                EntryImpactEvidence(
                    changed_function_id=change.function_id,
                    change_type=change.change_type,
                    reason=_impact_reason(entry_id, change),
                    old_paths=_paths_to(old_codebase, entry_id, change.function_id),
                    new_paths=_paths_to(new_codebase, entry_id, change.function_id),
                )
            )

    impacts = tuple(
        EntryImpact(
            entry_id=entry_id,
            old_entry=old_entries.get(entry_id),
            new_entry=new_entries.get(entry_id),
            evidence=tuple(
                sorted(
                    evidence,
                    key=lambda item: (
                        item.changed_function_id,
                        item.change_type.value,
                    ),
                )
            ),
        )
        for entry_id, evidence in sorted(evidence_by_entry.items())
    )
    return impacts, tuple(unassigned)


def _affected_entries(
    codebase: Codebase,
    entries: dict[str, object],
    function_id: str,
) -> set[str]:
    if codebase.get_function(function_id) is None:
        return set()
    affected = {function_id} if function_id in entries else set()
    affected.update(
        caller.id
        for caller in codebase.callers(function_id, transitive=True)
        if caller.id in entries
    )
    return affected


def _paths_to(
    codebase: Codebase,
    entry_id: str,
    function_id: str,
) -> tuple[tuple[str, ...], ...]:
    if (
        codebase.get_function(entry_id) is None
        or codebase.get_function(function_id) is None
    ):
        return ()
    context = codebase.dependency_context(entry_id)
    return tuple(path for path in context.paths if path[-1] == function_id)


def _impact_reason(entry_id: str, change: FunctionChange) -> str:
    subject = "entry" if entry_id == change.function_id else "reachable_function"
    return f"{subject}_{change.change_type.value}"
