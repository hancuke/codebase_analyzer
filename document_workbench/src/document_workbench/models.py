from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class WorkbenchPhase(Enum):
    SNAPSHOT_READY = "snapshot_ready"
    PROCESSING = "processing"
    COMPLETED = "completed"


@dataclass(frozen=True)
class UnitCompletion:
    unit_id: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class WorkbenchStatus:
    phase: WorkbenchPhase
    unit_ids: tuple[str, ...]
    pending_unit_ids: tuple[str, ...]
    completions: tuple[UnitCompletion, ...]

    @property
    def is_complete(self) -> bool:
        return self.phase is WorkbenchPhase.COMPLETED
