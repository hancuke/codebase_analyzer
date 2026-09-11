from __future__ import annotations

from xml.etree import ElementTree

from change_analyzer import CallEdgeChange, FunctionChange
from codegraph import AnalysisContext, Function

from .models import (
    ArchivePromptReference,
    CreatePromptReference,
    DocumentAction,
    DocumentEntryImpact,
    EntryDocumentPlan,
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
    PromptInstructions,
    PromptReference,
    ReviewPromptReference,
    UpdatePromptReference,
)


def build_prompt_payload(
    plan: EntryDocumentPlan, old_document: str
) -> LlmPromptPayload:
    return LlmPromptPayload(
        action=plan.action,
        instructions=_instructions_for(plan.action),
        reference_data=_reference_data_for(plan, old_document),
    )


def render_prompt_xml(payload: LlmPromptPayload) -> str:
    root = ElementTree.Element("document_prompt")
    instructions = ElementTree.SubElement(root, "instructions")
    _add_text(instructions, "objective", payload.instructions.objective)
    rules = ElementTree.SubElement(instructions, "rules")
    for rule in payload.instructions.rules:
        _add_text(rules, "rule", rule)

    reference = ElementTree.SubElement(root, "reference_data")
    _add_text(reference, "action", payload.action.value)
    _render_reference(reference, payload.reference_data)
    ElementTree.indent(root, space="  ")
    return ElementTree.tostring(root, encoding="unicode") + "\n"


def _instructions_for(action: DocumentAction) -> PromptInstructions:
    objectives = {
        DocumentAction.CREATE: "Create the entry-oriented code document.",
        DocumentAction.UPDATE: "Update the entry-oriented code document.",
        DocumentAction.ARCHIVE: "Archive the entry-oriented code document.",
        DocumentAction.REVIEW: "Review the entry-oriented code document manually.",
    }
    return PromptInstructions(
        objective=objectives[action],
        rules=(
            "Use only the supplied reference data.",
            "Do not invent behavior.",
            "Return only the resulting document content.",
        ),
    )


def _reference_data_for(
    plan: EntryDocumentPlan, old_document: str
) -> PromptReference:
    if plan.action is DocumentAction.CREATE:
        return CreatePromptReference(
            document_path=plan.document_path,
            covered_entry_ids=plan.covered_entry_ids,
            entries=tuple(
                PromptCreateEntry(
                    entry_id=entry.entry_id,
                    new_context=_required_context(
                        entry.new_context, plan.action, entry.entry_id
                    ),
                )
                for entry in plan.entries
            ),
        )
    if plan.action is DocumentAction.UPDATE:
        return UpdatePromptReference(
            document_path=plan.document_path,
            covered_entry_ids=plan.covered_entry_ids,
            existing_document=old_document,
            entries=tuple(_changed_entry(entry) for entry in plan.entries),
            function_changes=_function_changes(plan.function_changes),
            call_edge_changes=_call_edge_changes(plan.call_edge_changes),
        )
    if plan.action is DocumentAction.ARCHIVE:
        return ArchivePromptReference(
            document_path=plan.document_path,
            covered_entry_ids=plan.covered_entry_ids,
            existing_document=old_document,
            entries=tuple(
                PromptArchiveEntry(
                    entry_id=entry.entry_id,
                    old_context=_required_context(
                        entry.old_context, plan.action, entry.entry_id
                    ),
                    evidence=tuple(
                        PromptArchiveEvidence(
                            changed_function_id=item.changed_function_id,
                            reason=item.reason,
                            old_paths=item.old_paths,
                        )
                        for item in entry.impact.evidence
                    ),
                )
                for entry in plan.entries
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
        document_path=plan.document_path,
        covered_entry_ids=plan.covered_entry_ids,
        existing_document=old_document or None,
        entries=tuple(_changed_entry(entry) for entry in plan.entries),
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


def _changed_entry(entry: DocumentEntryImpact) -> PromptChangedEntry:
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
    _add_text(parent, "document_path", reference.document_path)
    covered_entries = ElementTree.SubElement(parent, "covered_entry_ids")
    for entry_id in reference.covered_entry_ids:
        _add_text(covered_entries, "entry_id", entry_id)

    if isinstance(reference, CreatePromptReference):
        _render_create_entries(parent, reference.entries)
        return

    if isinstance(reference, ArchivePromptReference):
        _add_text(parent, "existing_document", reference.existing_document)
        _render_archive_entries(parent, reference.entries)
        _render_function_changes(
            parent, "deleted_functions", reference.deleted_functions
        )
        _render_call_edge_changes(
            parent, "deleted_call_edges", reference.deleted_call_edges
        )
        return

    if isinstance(reference, UpdatePromptReference):
        _add_text(parent, "existing_document", reference.existing_document)
    elif reference.existing_document is not None:
        _add_text(parent, "existing_document", reference.existing_document)
    _render_changed_entries(parent, reference.entries)
    _render_function_changes(
        parent, "function_changes", reference.function_changes
    )
    _render_call_edge_changes(
        parent, "call_edge_changes", reference.call_edge_changes
    )


def _render_create_entries(
    parent: ElementTree.Element, entries: tuple[PromptCreateEntry, ...]
) -> None:
    container = ElementTree.SubElement(parent, "entries")
    for entry in entries:
        element = ElementTree.SubElement(container, "entry")
        _add_text(element, "entry_id", entry.entry_id)
        _render_context(element, "new_context", entry.new_context)


def _render_changed_entries(
    parent: ElementTree.Element, entries: tuple[PromptChangedEntry, ...]
) -> None:
    container = ElementTree.SubElement(parent, "entries")
    for entry in entries:
        element = ElementTree.SubElement(container, "entry")
        _add_text(element, "entry_id", entry.entry_id)
        if entry.old_context is not None:
            _render_context(element, "old_context", entry.old_context)
        if entry.new_context is not None:
            _render_context(element, "new_context", entry.new_context)
        _render_impact_evidence(element, entry.evidence)


def _render_archive_entries(
    parent: ElementTree.Element, entries: tuple[PromptArchiveEntry, ...]
) -> None:
    container = ElementTree.SubElement(parent, "entries")
    for entry in entries:
        element = ElementTree.SubElement(container, "entry")
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
