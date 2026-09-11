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
class PromptInstructions:
    objective: str
    rules: tuple[str, ...]


@dataclass(frozen=True)
class PromptFunction:
    function_id: str
    source_id: str
    source: str


@dataclass(frozen=True)
class PromptCodeContext:
    functions: tuple[PromptFunction, ...]


@dataclass(frozen=True)
class PromptImpactEvidence:
    changed_function_id: str
    reason: str
    old_paths: tuple[tuple[str, ...], ...]
    new_paths: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class PromptArchiveEvidence:
    changed_function_id: str
    reason: str
    old_paths: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class PromptFunctionChange:
    function_id: str
    change_type: str
    diff: str


@dataclass(frozen=True)
class PromptCallEdgeChange:
    source_function_id: str
    target_function_id: str
    change_type: str


@dataclass(frozen=True)
class PromptCreateEntry:
    entry_id: str
    new_context: PromptCodeContext


@dataclass(frozen=True)
class PromptChangedEntry:
    entry_id: str
    old_context: PromptCodeContext | None
    new_context: PromptCodeContext | None
    evidence: tuple[PromptImpactEvidence, ...]


@dataclass(frozen=True)
class PromptArchiveEntry:
    entry_id: str
    old_context: PromptCodeContext
    evidence: tuple[PromptArchiveEvidence, ...]


@dataclass(frozen=True)
class CreatePromptReference:
    document_path: str
    covered_entry_ids: tuple[str, ...]
    entries: tuple[PromptCreateEntry, ...]


@dataclass(frozen=True)
class UpdatePromptReference:
    document_path: str
    covered_entry_ids: tuple[str, ...]
    existing_document: str
    entries: tuple[PromptChangedEntry, ...]
    function_changes: tuple[PromptFunctionChange, ...]
    call_edge_changes: tuple[PromptCallEdgeChange, ...]


@dataclass(frozen=True)
class ArchivePromptReference:
    document_path: str
    covered_entry_ids: tuple[str, ...]
    existing_document: str
    entries: tuple[PromptArchiveEntry, ...]
    deleted_functions: tuple[PromptFunctionChange, ...]
    deleted_call_edges: tuple[PromptCallEdgeChange, ...]


@dataclass(frozen=True)
class ReviewPromptReference:
    document_path: str
    covered_entry_ids: tuple[str, ...]
    existing_document: str | None
    entries: tuple[PromptChangedEntry, ...]
    function_changes: tuple[PromptFunctionChange, ...]
    call_edge_changes: tuple[PromptCallEdgeChange, ...]


PromptReference = (
    CreatePromptReference
    | UpdatePromptReference
    | ArchivePromptReference
    | ReviewPromptReference
)


@dataclass(frozen=True)
class LlmPromptPayload:
    action: DocumentAction
    instructions: PromptInstructions
    reference_data: PromptReference

    def __post_init__(self) -> None:
        expected_reference_types = {
            DocumentAction.CREATE: CreatePromptReference,
            DocumentAction.UPDATE: UpdatePromptReference,
            DocumentAction.ARCHIVE: ArchivePromptReference,
            DocumentAction.REVIEW: ReviewPromptReference,
        }
        expected_type = expected_reference_types[self.action]
        if not isinstance(self.reference_data, expected_type):
            raise ValueError(
                f"{self.action.value} prompt requires "
                f"{expected_type.__name__}"
            )

    def to_xml(self) -> str:
        from .prompt import render_prompt_xml

        return render_prompt_xml(self)


@dataclass(frozen=True)
class LlmDocumentContext:
    plan: EntryDocumentPlan
    old_document: str

    def to_payload(self) -> LlmPromptPayload:
        from .prompt import build_prompt_payload

        return build_prompt_payload(self.plan, self.old_document)

    def to_prompt(self) -> str:
        return self.to_payload().to_xml()
