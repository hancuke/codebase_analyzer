from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from codegraph import Codebase, Diagnostic, EntryPoint, Function, SourceFile


class ChangeType(Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


@dataclass(frozen=True)
class SnapshotIssue:
    source_id: str
    side: str
    reason: str


@dataclass(frozen=True)
class SourceSnapshots:
    baseline: tuple[SourceFile, ...]
    working: tuple[SourceFile, ...]
    issues: tuple[SnapshotIssue, ...] = ()


@dataclass(frozen=True)
class FunctionChange:
    function_id: str
    change_type: ChangeType
    old_function: Function | None
    new_function: Function | None
    diff: str


@dataclass(frozen=True)
class CallEdgeChange:
    source_function_id: str
    target_function_id: str
    change_type: ChangeType


@dataclass(frozen=True)
class EntryImpactEvidence:
    changed_function_id: str
    change_type: ChangeType
    reason: str
    old_paths: tuple[tuple[str, ...], ...]
    new_paths: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class EntryImpact:
    entry_id: str
    old_entry: EntryPoint | None
    new_entry: EntryPoint | None
    evidence: tuple[EntryImpactEvidence, ...]


@dataclass(frozen=True)
class ImpactBatch:
    entry_ids: tuple[str, ...]
    changed_function_ids: tuple[str, ...]


@dataclass(frozen=True)
class ImpactReport:
    baseline_revision: str
    working_revision: str
    old_codebase: Codebase
    new_codebase: Codebase
    function_changes: tuple[FunctionChange, ...]
    call_edge_changes: tuple[CallEdgeChange, ...]
    entry_impacts: tuple[EntryImpact, ...]
    unassigned_changes: tuple[FunctionChange, ...]
    snapshot_issues: tuple[SnapshotIssue, ...]

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        return (*self.old_codebase.diagnostics, *self.new_codebase.diagnostics)
