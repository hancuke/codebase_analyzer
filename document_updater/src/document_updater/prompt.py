from __future__ import annotations

from xml.etree import ElementTree

from change_analyzer import CallEdgeChange, FunctionChange
from codegraph import AnalysisContext, Function

from .models import (
    ArchivePromptReference,
    CreatePromptReference,
    DocumentAction,
    EntryImpactContext,
    EntryUpdatePlan,
    LlmPromptPayload,
    PromptArchiveEntry,
    PromptArchiveEvidence,
    PromptCallEdgeChange,
    PromptChangedEntry,
    PromptCodeContext,
    PromptCreateEntry,
    PromptFunction,
    PromptFunctionChange,
    PromptImpactEvidence,
    PromptReference,
    ReviewPromptReference,
    UpdatePromptReference,
)


def build_prompt_payload(
    plan: EntryUpdatePlan, old_document: str
) -> LlmPromptPayload:
    return LlmPromptPayload(
        action=plan.action,
        reference_data=_reference_data_for(plan, old_document),
    )


def render_reference_data_xml(payload: LlmPromptPayload) -> str:
    root = ElementTree.Element("reference_data")
    _add_text(root, "action", payload.action.value)
    _render_reference(root, payload.reference_data)
    ElementTree.indent(root, space="  ")
    return ElementTree.tostring(root, encoding="unicode") + "\n"


def _reference_data_for(
    plan: EntryUpdatePlan, old_document: str
) -> PromptReference:
    if plan.action is DocumentAction.CREATE:
        return CreatePromptReference(
            entry=PromptCreateEntry(
                entry_id=plan.entry_id,
                new_context=_required_context(
                    plan.entry.new_context, plan.action, plan.entry_id
                ),
            ),
        )
    if plan.action is DocumentAction.UPDATE:
        return UpdatePromptReference(
            existing_document=old_document or None,
            entry=_changed_entry(plan.entry),
            function_changes=_function_changes(plan.function_changes),
            call_edge_changes=_call_edge_changes(plan.call_edge_changes),
        )
    if plan.action is DocumentAction.ARCHIVE:
        return ArchivePromptReference(
            existing_document=old_document or None,
            entry=PromptArchiveEntry(
                entry_id=plan.entry_id,
                old_context=_required_context(
                    plan.entry.old_context, plan.action, plan.entry_id
                ),
                evidence=tuple(
                    PromptArchiveEvidence(
                        changed_function_id=item.changed_function_id,
                        reason=item.reason,
                        old_paths=item.old_paths,
                    )
                    for item in plan.entry.impact.evidence
                ),
            ),
            deleted_functions=_function_changes(
                tuple(
                    change
                    for change in plan.function_changes
                    if change.change_type.value == "deleted"
                )
            ),
            deleted_call_edges=_call_edge_changes(
                tuple(
                    change
                    for change in plan.call_edge_changes
                    if change.change_type.value == "deleted"
                )
            ),
        )
    return ReviewPromptReference(
        existing_document=old_document or None,
        entry=_changed_entry(plan.entry),
        function_changes=_function_changes(plan.function_changes),
        call_edge_changes=_call_edge_changes(plan.call_edge_changes),
    )


def _required_context(
    context: AnalysisContext | None,
    action: DocumentAction,
    entry_id: str,
) -> PromptCodeContext:
    if context is None:
        raise ValueError(
            f"{action.value} prompt requires context for entry {entry_id!r}"
        )
    return _code_context(context)


def _changed_entry(entry: EntryImpactContext) -> PromptChangedEntry:
    return PromptChangedEntry(
        entry_id=entry.entry_id,
        old_context=(
            _code_context(entry.old_context)
            if entry.old_context is not None
            else None
        ),
        new_context=(
            _code_context(entry.new_context)
            if entry.new_context is not None
            else None
        ),
        evidence=tuple(
            PromptImpactEvidence(
                changed_function_id=item.changed_function_id,
                reason=item.reason,
                old_paths=item.old_paths,
                new_paths=item.new_paths,
            )
            for item in entry.impact.evidence
        ),
    )


def _code_context(context: AnalysisContext) -> PromptCodeContext:
    return PromptCodeContext(
        functions=tuple(_prompt_function(function) for function in context.functions)
    )


def _prompt_function(function: Function) -> PromptFunction:
    return PromptFunction(
        function_id=function.id,
        source_id=function.source_id,
        source=function.source,
    )


def _function_changes(
    changes: tuple[FunctionChange, ...],
) -> tuple[PromptFunctionChange, ...]:
    return tuple(
        PromptFunctionChange(
            function_id=change.function_id,
            change_type=change.change_type.value,
            diff=change.diff,
        )
        for change in changes
    )


def _call_edge_changes(
    changes: tuple[CallEdgeChange, ...],
) -> tuple[PromptCallEdgeChange, ...]:
    return tuple(
        PromptCallEdgeChange(
            source_function_id=change.source_function_id,
            target_function_id=change.target_function_id,
            change_type=change.change_type.value,
        )
        for change in changes
    )


def _render_reference(
    parent: ElementTree.Element, reference: PromptReference
) -> None:
    if isinstance(reference, CreatePromptReference):
        _render_create_entry(parent, reference.entry)
        return

    if isinstance(reference, ArchivePromptReference):
        if reference.existing_document is not None:
            _add_text(parent, "existing_document", reference.existing_document)
        _render_archive_entry(parent, reference.entry)
        _render_function_changes(
            parent, "deleted_functions", reference.deleted_functions
        )
        _render_call_edge_changes(
            parent, "deleted_call_edges", reference.deleted_call_edges
        )
        return

    if reference.existing_document is not None:
        _add_text(parent, "existing_document", reference.existing_document)
    _render_changed_entry(parent, reference.entry)
    _render_function_changes(
        parent, "function_changes", reference.function_changes
    )
    _render_call_edge_changes(
        parent, "call_edge_changes", reference.call_edge_changes
    )


def _render_create_entry(
    parent: ElementTree.Element, entry: PromptCreateEntry
) -> None:
    element = ElementTree.SubElement(parent, "entry")
    _add_text(element, "entry_id", entry.entry_id)
    _render_context(element, "new_context", entry.new_context)


def _render_changed_entry(
    parent: ElementTree.Element, entry: PromptChangedEntry
) -> None:
    element = ElementTree.SubElement(parent, "entry")
    _add_text(element, "entry_id", entry.entry_id)
    if entry.old_context is not None:
        _render_context(element, "old_context", entry.old_context)
    if entry.new_context is not None:
        _render_context(element, "new_context", entry.new_context)
    _render_impact_evidence(element, entry.evidence)


def _render_archive_entry(
    parent: ElementTree.Element, entry: PromptArchiveEntry
) -> None:
    element = ElementTree.SubElement(parent, "entry")
    _add_text(element, "entry_id", entry.entry_id)
    _render_context(element, "old_context", entry.old_context)
    evidence = ElementTree.SubElement(element, "impact_evidence")
    for item in entry.evidence:
        evidence_element = ElementTree.SubElement(evidence, "change")
        _add_text(
            evidence_element,
            "changed_function_id",
            item.changed_function_id,
        )
        _add_text(evidence_element, "reason", item.reason)
        _render_paths(evidence_element, "old_paths", item.old_paths)


def _render_context(
    parent: ElementTree.Element, tag: str, context: PromptCodeContext
) -> None:
    element = ElementTree.SubElement(parent, tag)
    functions = ElementTree.SubElement(element, "functions")
    for function in context.functions:
        function_element = ElementTree.SubElement(functions, "function")
        _add_text(function_element, "function_id", function.function_id)
        _add_text(function_element, "source_id", function.source_id)
        _add_text(function_element, "source", function.source)


def _render_impact_evidence(
    parent: ElementTree.Element, evidence: tuple[PromptImpactEvidence, ...]
) -> None:
    container = ElementTree.SubElement(parent, "impact_evidence")
    for item in evidence:
        element = ElementTree.SubElement(container, "change")
        _add_text(element, "changed_function_id", item.changed_function_id)
        _add_text(element, "reason", item.reason)
        _render_paths(element, "old_paths", item.old_paths)
        _render_paths(element, "new_paths", item.new_paths)


def _render_paths(
    parent: ElementTree.Element,
    tag: str,
    paths: tuple[tuple[str, ...], ...],
) -> None:
    container = ElementTree.SubElement(parent, tag)
    for path in paths:
        path_element = ElementTree.SubElement(container, "path")
        for function_id in path:
            _add_text(path_element, "function_id", function_id)


def _render_function_changes(
    parent: ElementTree.Element,
    tag: str,
    changes: tuple[PromptFunctionChange, ...],
) -> None:
    container = ElementTree.SubElement(parent, tag)
    for change in changes:
        element = ElementTree.SubElement(container, "function_change")
        _add_text(element, "function_id", change.function_id)
        _add_text(element, "change_type", change.change_type)
        _add_text(element, "diff", change.diff)


def _render_call_edge_changes(
    parent: ElementTree.Element,
    tag: str,
    changes: tuple[PromptCallEdgeChange, ...],
) -> None:
    container = ElementTree.SubElement(parent, tag)
    for change in changes:
        element = ElementTree.SubElement(container, "call_edge_change")
        _add_text(element, "source_function_id", change.source_function_id)
        _add_text(element, "target_function_id", change.target_function_id)
        _add_text(element, "change_type", change.change_type)


def _add_text(
    parent: ElementTree.Element, tag: str, value: str | None
) -> ElementTree.Element:
    element = ElementTree.SubElement(parent, tag)
    element.text = value
    return element
