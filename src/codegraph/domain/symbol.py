from dataclasses import dataclass
from enum import Enum


class SymbolKind(str, Enum):
    FUNCTION = "function"
    METHOD = "method"
    SUB = "sub"
    EVENT = "event"
    CLASS = "class"


@dataclass(frozen=True)
class SymbolId:
    value: str

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Symbol:
    id: SymbolId
    name: str
    qualified_name: str
    kind: SymbolKind
    language: str
    module: str
    file: str

