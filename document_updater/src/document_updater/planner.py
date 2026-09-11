from __future__ import annotations

from collections.abc import Iterable

from change_analyzer import CallEdgeChange, EntryImpact, ImpactReport
from codegraph import AnalysisContext, Codebase, Diagnostic

from .models import (
    DocumentAction,
    EntryImpactContext,
    EntryUpdatePlan,
    LlmEntryContext,
)


def create_entry_plans(
    report: ImpactReport,
) -> tuple[EntryUpdatePlan, ...]:
    function_changes = {
        change.function_id: change for change in report.function_changes
    }
    plans: list[EntryUpdatePlan] = []
    for impact in sorted(report.entry_impacts, key=lambda item: item.entry_id):
        entry = EntryImpactContext(
            impact=impact,
            old_context=_context_or_none(report.old_codebase, impact.entry_id),
            new_context=_context_or_none(report.new_codebase, impact.entry_id),
        )
        related_ids = {
            evidence.changed_function_id
            for evidence in entry.impact.evidence
        }
        context_ids = {
            function.id
            for context in (entry.old_context, entry.new_context)
            if context is not None
            for function in context.functions
        }
        edge_changes = _deduplicate_edge_changes(
            change
            for change in report.call_edge_changes
            if change.source_function_id in context_ids
            or change.target_function_id in context_ids
        )
        diagnostics = _deduplicate_diagnostics(
            diagnostic
            for context in (entry.old_context, entry.new_context)
            if context is not None
            for diagnostic in context.diagnostics
        )
        action = _entry_action(report, entry.entry_id)
        if any(diagnostic.severity == "error" for diagnostic in diagnostics):
            action = DocumentAction.REVIEW
        plans.append(
            EntryUpdatePlan(
                action=action,
                entry=entry,
                function_changes=tuple(
                    function_changes[function_id]
                    for function_id in sorted(related_ids)
                ),
                call_edge_changes=edge_changes,
                diagnostics=diagnostics,
            )
        )
    return tuple(plans)


def build_llm_context(
    plan: EntryUpdatePlan,
    old_document: str = "",
) -> LlmEntryContext:
    return LlmEntryContext(plan=plan, old_document=old_document)


def _context_or_none(
    codebase: Codebase, entry_id: str
) -> AnalysisContext | None:
    if codebase.get_function(entry_id) is None:
        return None
    return codebase.dependency_context(entry_id)


def _entry_action(report: ImpactReport, entry_id: str) -> DocumentAction:
    old_exists = report.old_codebase.get_function(entry_id) is not None
    new_exists = report.new_codebase.get_function(entry_id) is not None
    if not old_exists:
        return DocumentAction.CREATE
    if not new_exists:
        return DocumentAction.ARCHIVE
    return DocumentAction.UPDATE


def _deduplicate_edge_changes(
    changes: Iterable[CallEdgeChange],
) -> tuple[CallEdgeChange, ...]:
    unique = {
        (
            change.source_function_id,
            change.target_function_id,
            change.change_type.value,
        ): change
        for change in changes
    }
    return tuple(unique[key] for key in sorted(unique))


def _deduplicate_diagnostics(
    diagnostics: Iterable[Diagnostic],
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
        for item in diagnostics
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
