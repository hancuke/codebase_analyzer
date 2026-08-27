from ..domain.entrypoint import EntryPoint
from ..domain.symbol import SymbolId
from ..graph.dependency import DependencyGraph


def affected_entry_points(
    graph: DependencyGraph,
    changed: SymbolId,
    entry_points: list[EntryPoint],
) -> list[EntryPoint]:
    affected = {changed, *graph.ancestors_of(changed)}
    return [entry for entry in entry_points if entry.symbol in affected]
