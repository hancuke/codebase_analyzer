from .analysis.dependency import analyze_entry_point
from .analysis.impact import affected_entry_points
from .domain.entrypoint import AnalysisContext, EntryPoint
from .domain.reference import (
    Reference,
    ReferenceKind,
    ResolvedReference,
    ResolveResult,
    ResolveStatus,
    SourceLocation,
)
from .domain.symbol import Symbol, SymbolId, SymbolKind
from .graph.dependency import DependencyGraph, DependencyPath
from .parser.java import JavaParser
from .parser.vba import VbaParser
from .project import CodeProject
from .repository.symbol import InMemorySymbolRepository, SymbolRepository
from .resolver.simple import SimpleResolver
from .builder import DependencyBuilder

__all__ = [
    "AnalysisContext", "CodeProject", "DependencyBuilder", "DependencyGraph",
    "DependencyPath", "EntryPoint", "InMemorySymbolRepository", "JavaParser",
    "Reference", "ReferenceKind", "ResolvedReference", "ResolveResult",
    "ResolveStatus", "SimpleResolver", "SourceLocation", "Symbol", "SymbolId",
    "SymbolKind", "SymbolRepository", "VbaParser", "affected_entry_points",
    "analyze_entry_point",
]
