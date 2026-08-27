from .domain.reference import ResolvedReference, ResolveStatus
from .domain.symbol import Symbol
from .parser.base import Parser
from .repository.symbol import SymbolRepository
from .resolver.simple import SimpleResolver


class DependencyBuilder:
    def __init__(
        self,
        parser: Parser,
        resolver: SimpleResolver,
        repository: SymbolRepository,
    ) -> None:
        self.parser = parser
        self.resolver = resolver
        self.repository = repository

    def build(self, symbol: Symbol, source: str) -> list[ResolvedReference]:
        resolved_references: list[ResolvedReference] = []
        for reference in self.parser.parse(symbol, source):
            result = self.resolver.resolve(reference, self.repository)
            if result.status is ResolveStatus.RESOLVED and result.target is not None:
                resolved_references.append(ResolvedReference(
                    source=reference.source,
                    target=result.target,
                    kind=reference.kind,
                    location=reference.location,
                ))
        return resolved_references
