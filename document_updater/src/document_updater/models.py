from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from change_analyzer import CallEdgeChange, EntryImpact, FunctionChange
from codegraph import AnalysisContext, Diagnostic, EntryPoint


class DocumentAction(Enum):
    CREATE = "create"
    UPDATE = "update"
    ARCHIVE = "archive"
    REVIEW = "review"


@dataclass(frozen=True)
class DocumentEntryImpact:
    """One affected entry's evidence and contexts within a document plan."""

    impact: EntryImpact
    old_context: AnalysisContext | None
    new_context: AnalysisContext | None

    @property
    def entry_id(self) -> str:
        return self.impact.entry_id

    @property
    def old_entry(self) -> EntryPoint | None:
        return self.impact.old_entry

    @property
    def new_entry(self) -> EntryPoint | None:
        return self.impact.new_entry


@dataclass(frozen=True)
class EntryDocumentPlan:
    """One deterministic document update work item."""

    document_path: str
    action: DocumentAction
    covered_entry_ids: tuple[str, ...]
    entries: tuple[DocumentEntryImpact, ...]
    function_changes: tuple[FunctionChange, ...]
    call_edge_changes: tuple[CallEdgeChange, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def entry_ids(self) -> tuple[str, ...]:
        return tuple(entry.entry_id for entry in self.entries)

    @property
    def entry_id(self) -> str:
        """Compatibility accessor for callers that still expect one affected entry."""
        return self.entries[0].entry_id


@dataclass(frozen=True)
class LlmDocumentContext:
    plan: EntryDocumentPlan
    old_document: str

    def to_prompt(self) -> str:
        sections = [
            "Update the entry-oriented code document from the supplied facts.",
            "Preserve still-correct content and do not invent behavior.",
            f"Action: {self.plan.action.value}",
            f"Document: {self.plan.document_path}",
            f"Covered entries: {', '.join(self.plan.covered_entry_ids)}",
            "",
            "## Existing document",
            self.old_document or "(none)",
            "",
            "## Affected entries",
            _format_entry_impacts(self.plan.entries),
            "",
            "## Function changes",
            _format_function_changes(self.plan.function_changes),
            "",
            "## Call-edge changes",
            _format_edge_changes(self.plan.call_edge_changes),
            "",
            "## Diagnostics",
            _format_diagnostics(self.plan.diagnostics),
        ]
        return "\n".join(sections).rstrip() + "\n"


def _format_function_changes(changes: tuple[FunctionChange, ...]) -> str:
    if not changes:
        return "(none)"
    return "\n\n".join(
        f"### {change.function_id} [{change.change_type.value}]\n"
        f"```diff\n{change.diff.rstrip()}\n```"
        for change in changes
    )


def _format_edge_changes(changes: tuple[CallEdgeChange, ...]) -> str:
    if not changes:
        return "(none)"
    return "\n".join(
        f"- [{change.change_type.value}] "
        f"{change.source_function_id} -> {change.target_function_id}"
        for change in changes
    )


def _format_entry_impacts(entries: tuple[DocumentEntryImpact, ...]) -> str:
    if not entries:
        return "(none)"
    lines: list[str] = []
    for entry in entries:
        lines.extend(
            [
                f"### {entry.entry_id}",
                "#### Impact evidence",
                _format_evidence(entry.impact),
                "#### Baseline entry context",
                _format_analysis_context(entry.old_context),
                "#### Working entry context",
                _format_analysis_context(entry.new_context),
            ]
        )
    return "\n".join(lines)


def _format_evidence(impact: EntryImpact) -> str:
    if not impact.evidence:
        return "(none)"
    return "\n".join(
        f"- {item.changed_function_id}: {item.reason}; "
        f"old_paths={_format_paths(item.old_paths)}; "
        f"new_paths={_format_paths(item.new_paths)}"
        for item in impact.evidence
    )


def _format_paths(paths: tuple[tuple[str, ...], ...]) -> str:
    if not paths:
        return "none"
    return " | ".join(" -> ".join(path) for path in paths)


def _format_analysis_context(context: AnalysisContext | None) -> str:
    if context is None:
        return "(entry absent)"
    return "\n\n".join(
        f"### {function.id}\n```text\n{function.source.rstrip()}\n```"
        for function in context.functions
    )


def _format_diagnostics(diagnostics: tuple[Diagnostic, ...]) -> str:
    if not diagnostics:
        return "(none)"
    return "\n".join(
        f"- [{diagnostic.severity}] {diagnostic.code}: {diagnostic.message}"
        for diagnostic in diagnostics
    )
