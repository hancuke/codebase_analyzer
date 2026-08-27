from typing import Protocol

from ..domain.reference import Reference, ResolveResult
from ..repository.symbol import SymbolRepository


class Resolver(Protocol):
    def resolve(
        self,
        reference: Reference,
        repository: SymbolRepository,
    ) -> ResolveResult: ...
