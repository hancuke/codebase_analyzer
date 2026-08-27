from dataclasses import dataclass

from ..domain.reference import ResolvedReference
from ..domain.symbol import SymbolId


@dataclass(frozen=True)
class DependencyPath:
    nodes: list[SymbolId]


class DependencyGraph:
    def __init__(self) -> None:
        self._outgoing: dict[SymbolId, set[SymbolId]] = {}
        self._incoming: dict[SymbolId, set[SymbolId]] = {}

    def add(self, reference: ResolvedReference) -> None:
        self._outgoing.setdefault(reference.source, set()).add(reference.target)
        self._incoming.setdefault(reference.target, set()).add(reference.source)

    def remove(self, reference: ResolvedReference) -> None:
        targets = self._outgoing.get(reference.source)
        if targets is not None:
            targets.discard(reference.target)
            if not targets:
                self._outgoing.pop(reference.source, None)
        sources = self._incoming.get(reference.target)
        if sources is not None:
            sources.discard(reference.source)
            if not sources:
                self._incoming.pop(reference.target, None)

    def replace_outgoing(
        self,
        source: SymbolId,
        references: list[ResolvedReference],
    ) -> None:
        for target in tuple(self._outgoing.get(source, ())):
            targets = self._outgoing.get(source)
            if targets is not None:
                targets.discard(target)
            sources = self._incoming.get(target)
            if sources is not None:
                sources.discard(source)
                if not sources:
                    self._incoming.pop(target, None)
        self._outgoing.pop(source, None)
        for reference in references:
            if reference.source != source:
                raise ValueError("replace_outgoing references must have the given source")
            self.add(reference)

    def dependencies_of(self, symbol: SymbolId) -> set[SymbolId]:
        return set(self._outgoing.get(symbol, ()))

    def dependents_of(self, symbol: SymbolId) -> set[SymbolId]:
        return set(self._incoming.get(symbol, ()))

    def descendants_of(self, symbol: SymbolId) -> set[SymbolId]:
        return self._walk(symbol, self.dependencies_of)

    def ancestors_of(self, symbol: SymbolId) -> set[SymbolId]:
        return self._walk(symbol, self.dependents_of)

    def paths_from(self, symbol: SymbolId) -> list[DependencyPath]:
        paths: list[DependencyPath] = []

        def visit(current: SymbolId, path: list[SymbolId]) -> None:
            dependencies = self.dependencies_of(current)
            if not dependencies:
                paths.append(DependencyPath(path))
                return
            progressed = False
            for dependency in sorted(dependencies, key=str):
                if dependency in path:
                    continue
                progressed = True
                visit(dependency, [*path, dependency])
            if not progressed:
                paths.append(DependencyPath(path))

        visit(symbol, [symbol])
        return paths

    @staticmethod
    def _walk(start: SymbolId, neighbors) -> set[SymbolId]:
        visited: set[SymbolId] = {start}
        stack = [start]
        while stack:
            current = stack.pop()
            for neighbor in neighbors(current):
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        visited.remove(start)
        return visited
