from ..domain.entrypoint import AnalysisContext
from ..domain.symbol import SymbolId
from ..graph.dependency import DependencyGraph
from ..repository.symbol import SymbolRepository


def analyze_entry_point(
    graph: DependencyGraph,
    entry_point: SymbolId,
    repository: SymbolRepository | None = None,
) -> AnalysisContext:
    ids = {entry_point, *graph.descendants_of(entry_point)}
    symbols = []
    if repository is not None:
        symbols = [symbol for symbol in repository.all() if symbol.id in ids]
        symbols.sort(key=lambda symbol: symbol.id.value)
    return AnalysisContext(
        entry_point=entry_point,
        symbols=symbols,
        paths=graph.paths_from(entry_point),
    )
