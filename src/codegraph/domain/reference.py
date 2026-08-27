from dataclasses import dataclass
from enum import Enum

from .symbol import SymbolId


class ReferenceKind(str, Enum):
    CALL = "call"
    EVENT = "event"
    INHERIT = "inherit"
    IMPLEMENT = "implement"
    USE = "use"


@dataclass(frozen=True)
class SourceLocation:
    file: str
    line: int
    column: int | None = None


@dataclass(frozen=True)
class Reference:
    source: SymbolId
    target_name: str
    kind: ReferenceKind
    location: SourceLocation | None = None


class ResolveStatus(str, Enum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"
    EXTERNAL = "external"


@dataclass(frozen=True)
class ResolveResult:
    status: ResolveStatus
    target: SymbolId | None = None
    candidates: tuple[SymbolId, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class ResolvedReference:
    source: SymbolId
    target: SymbolId
    kind: ReferenceKind
    location: SourceLocation | None = None

