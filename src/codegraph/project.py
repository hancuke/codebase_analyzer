from collections.abc import Mapping

from .analysis.dependency import analyze_entry_point as build_context
from .analysis.impact import affected_entry_points
from .builder import DependencyBuilder
from .domain.entrypoint import AnalysisContext, EntryPoint
from .domain.symbol import Symbol, SymbolId
from .graph.dependency import DependencyGraph
from .repository.symbol import InMemorySymbolRepository


class CodeProject:
    def __init__(
        self,
        symbols: list[Symbol] | None = None,
        sources: Mapping[SymbolId, str] | None = None,
        builder: DependencyBuilder | None = None,
    ) -> None:
        self.repository = InMemorySymbolRepository(symbols or ())
        self.sources: dict[SymbolId, str] = dict(sources or {})
        self.graph = DependencyGraph()
        self.builder = builder
        self.entry_points: list[EntryPoint] = []

    def add_symbol(self, symbol: Symbol, source: str | None = None) -> None:
        self.repository.add(symbol)
        if source is not None:
            self.sources[symbol.id] = source

    def add_entry_point(self, entry_point: EntryPoint) -> None:
        if entry_point not in self.entry_points:
            self.entry_points.append(entry_point)

    def rebuild(self, symbol: SymbolId) -> None:
        if self.builder is None:
            raise ValueError("A DependencyBuilder is required to build dependencies")
        source = self.sources.get(symbol)
        if source is None:
            raise KeyError(f"No source registered for {symbol}")
        current = self.repository.get(symbol)
        if current is None:
            raise KeyError(f"Unknown symbol: {symbol}")
        self.graph.replace_outgoing(symbol, self.builder.build(current, source))

    def rebuild_all(self) -> None:
        for symbol_id in tuple(self.sources):
            self.rebuild(symbol_id)

    def dependencies_of(self, symbol: SymbolId):
        return self.graph.dependencies_of(symbol)

    def dependents_of(self, symbol: SymbolId):
        return self.graph.dependents_of(symbol)

    def descendants_of(self, symbol: SymbolId):
        return self.graph.descendants_of(symbol)

    def ancestors_of(self, symbol: SymbolId):
        return self.graph.ancestors_of(symbol)

    def analyze_entry_point(self, entry_point: SymbolId) -> AnalysisContext:
        return build_context(self.graph, entry_point, self.repository)

    def analyze_impact(self, changed: SymbolId) -> list[EntryPoint]:
        return affected_entry_points(self.graph, changed, self.entry_points)
