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
class EntryImpactContext:
    """One affected entry's evidence and dependency contexts."""

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
class EntryUpdatePlan:
    """One deterministic, entry-scoped update work item."""

    action: DocumentAction
    entry: EntryImpactContext
    function_changes: tuple[FunctionChange, ...]
    call_edge_changes: tuple[CallEdgeChange, ...]
    diagnostics: tuple[Diagnostic, ...]

    @property
    def entry_id(self) -> str:
        return self.entry.entry_id


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
    entry: PromptCreateEntry


@dataclass(frozen=True)
class UpdatePromptReference:
    existing_document: str | None
    entry: PromptChangedEntry
    function_changes: tuple[PromptFunctionChange, ...]
    call_edge_changes: tuple[PromptCallEdgeChange, ...]


@dataclass(frozen=True)
class ArchivePromptReference:
    existing_document: str | None
    entry: PromptArchiveEntry
    deleted_functions: tuple[PromptFunctionChange, ...]
    deleted_call_edges: tuple[PromptCallEdgeChange, ...]


@dataclass(frozen=True)
class ReviewPromptReference:
    existing_document: str | None
    entry: PromptChangedEntry
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

    def to_reference_data_xml(self) -> str:
        from .prompt import render_reference_data_xml

        return render_reference_data_xml(self)


@dataclass(frozen=True)
class LlmEntryContext:
    plan: EntryUpdatePlan
    old_document: str

    def to_payload(self) -> LlmPromptPayload:
        from .prompt import build_prompt_payload

        return build_prompt_payload(self.plan, self.old_document)

    def to_reference_data_xml(self) -> str:
        return self.to_payload().to_reference_data_xml()
