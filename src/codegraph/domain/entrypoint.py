from dataclasses import dataclass, field

from .symbol import Symbol, SymbolId
from ..graph.dependency import DependencyPath


@dataclass(frozen=True)
class EntryPoint:
    symbol: SymbolId
    kind: str


@dataclass
class AnalysisContext:
    entry_point: SymbolId
    symbols: list[Symbol] = field(default_factory=list)
    paths: list[DependencyPath] = field(default_factory=list)

