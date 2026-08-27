from collections.abc import Iterable
from typing import Protocol

from ..domain.symbol import Symbol, SymbolId


class SymbolRepository(Protocol):
    def get(self, symbol_id: SymbolId) -> Symbol | None: ...
    def find_by_name(self, name: str) -> list[Symbol]: ...
    def add(self, symbol: Symbol) -> None: ...
    def remove(self, symbol_id: SymbolId) -> None: ...
    def all(self) -> Iterable[Symbol]: ...


class InMemorySymbolRepository:
    def __init__(self, symbols: Iterable[Symbol] = ()) -> None:
        self._symbols: dict[SymbolId, Symbol] = {}
        self._by_name: dict[str, list[SymbolId]] = {}
        for symbol in symbols:
            self.add(symbol)

    def add(self, symbol: Symbol) -> None:
        if symbol.id in self._symbols:
            self.remove(symbol.id)
        self._symbols[symbol.id] = symbol
        self._by_name.setdefault(symbol.name, []).append(symbol.id)

    def get(self, symbol_id: SymbolId) -> Symbol | None:
        return self._symbols.get(symbol_id)

    def find_by_name(self, name: str) -> list[Symbol]:
        return [self._symbols[sid] for sid in self._by_name.get(name, [])]

    def remove(self, symbol_id: SymbolId) -> None:
        symbol = self._symbols.pop(symbol_id, None)
        if symbol is None:
            return
        ids = self._by_name.get(symbol.name, [])
        if symbol_id in ids:
            ids.remove(symbol_id)
        if not ids:
            self._by_name.pop(symbol.name, None)

    def all(self) -> Iterable[Symbol]:
        return self._symbols.values()
