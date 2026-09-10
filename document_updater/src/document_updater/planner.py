from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Iterable, Mapping

from change_analyzer import CallEdgeChange, EntryImpact, ImpactReport
from codegraph import AnalysisContext, Codebase, Diagnostic

from .catalog import DocumentCatalog
from .models import (
    DocumentAction,
    DocumentEntryImpact,
    EntryDocumentPlan,
    LlmDocumentContext,
)


def create_document_plans(
    report: ImpactReport,
    document_registry: DocumentCatalog | Mapping[str, str] | None = None,
) -> tuple[EntryDocumentPlan, ...]:
    catalog = _catalog_or_empty(document_registry)
    function_changes = {
        change.function_id: change for change in report.function_changes
    }
    plans: list[EntryDocumentPlan] = []
    impacts_by_path: dict[str, list[EntryImpact]] = defaultdict(list)
    for impact in report.entry_impacts:
        document_path = (
            catalog.document_path_for(impact.entry_id)
            or _default_document_path(impact.entry_id)
        )
        impacts_by_path[document_path].append(impact)

    for document_path, impacts in sorted(impacts_by_path.items()):
        entries = tuple(
            DocumentEntryImpact(
                impact=impact,
                old_context=_context_or_none(report.old_codebase, impact.entry_id),
                new_context=_context_or_none(report.new_codebase, impact.entry_id),
            )
            for impact in sorted(impacts, key=lambda item: item.entry_id)
        )
        related_ids = {
            evidence.changed_function_id
            for entry in entries
            for evidence in entry.impact.evidence
        }
        context_ids = {
            function.id
            for entry in entries
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
            for entry in entries
            for context in (entry.old_context, entry.new_context)
            if context is not None
            for diagnostic in context.diagnostics
        )
        covered_entry_ids = catalog.entry_ids_for(document_path) or tuple(
            entry.entry_id for entry in entries
        )
        action = _document_action(report, covered_entry_ids)
        if any(diagnostic.severity == "error" for diagnostic in diagnostics):
            action = DocumentAction.REVIEW
        plans.append(
            EntryDocumentPlan(
                document_path=document_path,
                action=action,
                covered_entry_ids=covered_entry_ids,
                entries=entries,
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
    plan: EntryDocumentPlan,
    old_document: str = "",
) -> LlmDocumentContext:
    return LlmDocumentContext(plan=plan, old_document=old_document)


def _context_or_none(
    codebase: Codebase, entry_id: str
) -> AnalysisContext | None:
    if codebase.get_function(entry_id) is None:
        return None
    return codebase.dependency_context(entry_id)


def _catalog_or_empty(
    document_registry: DocumentCatalog | Mapping[str, str] | None,
) -> DocumentCatalog:
    if document_registry is None:
        return DocumentCatalog()
    if isinstance(document_registry, DocumentCatalog):
        return document_registry
    return DocumentCatalog.from_mapping(document_registry)


def _document_action(
    report: ImpactReport, covered_entry_ids: tuple[str, ...]
) -> DocumentAction:
    old_exists = any(
        report.old_codebase.get_function(entry_id) is not None
        for entry_id in covered_entry_ids
    )
    new_exists = any(
        report.new_codebase.get_function(entry_id) is not None
        for entry_id in covered_entry_ids
    )
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


def _default_document_path(entry_id: str) -> str:
    parts = entry_id.split(":")
    if len(parts) >= 3:
        language, module, name = parts[0], parts[1], ":".join(parts[2:])
        return (
            f"docs/entries/{_safe_component(language)}/"
            f"{_safe_component(module)}/{_safe_component(name)}.md"
        )
    return f"docs/entries/{_safe_component(entry_id)}.md"


def _safe_component(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-") or "entry"


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
