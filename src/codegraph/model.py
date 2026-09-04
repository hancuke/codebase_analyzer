from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class SourceFile:
    source_id: str
    content: str
    language: str | None = None


@dataclass(frozen=True)
class ContextLimits:
    max_depth: int | None = None
    max_functions: int | None = None
    max_source_chars: int | None = None


@dataclass(frozen=True)
class SourceRange:
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Function:
    id: str
    name: str
    language: str
    module: str
    source_id: str
    source: str
    source_range: SourceRange
    attributes: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "attributes", MappingProxyType(dict(self.attributes)))

    @property
    def qualified_name(self) -> str:
        return f"{self.module}.{self.name}"


@dataclass(frozen=True)
class Call:
    source_id: str
    name: str
    line: int
    target_id: str | None = None
    evidence: str | None = None


@dataclass(frozen=True)
class Diagnostic:
    code: str
    severity: str
    message: str
    source_id: str | None = None
    function_id: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class EntryPoint:
    function_id: str
    kind: str
    source: str


@dataclass(frozen=True)
class AnalysisResult:
    functions: tuple[Function, ...] = ()
    calls: tuple[Call, ...] = ()
    entry_points: tuple[EntryPoint, ...] = ()
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True)
class AnalysisContext:
    entry: EntryPoint
    functions: tuple[Function, ...]
    calls: tuple[Call, ...]
    paths: tuple[tuple[str, ...], ...]
    diagnostics: tuple[Diagnostic, ...]
    truncated: bool = False
    truncation_reasons: tuple[str, ...] = ()
