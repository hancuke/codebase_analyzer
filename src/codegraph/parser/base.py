from typing import Protocol

from ..domain.reference import Reference
from ..domain.symbol import Symbol


class Parser(Protocol):
    def parse(self, symbol: Symbol, source: str) -> list[Reference]: ...
