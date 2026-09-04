from __future__ import annotations

from collections import defaultdict, deque
from typing import Sequence

from .analyzer import LanguageAnalyzer
from .model import (
    AnalysisContext,
    Call,
    ContextLimits,
    Diagnostic,
    EntryPoint,
    AnalysisResult,
    Function,
    SourceFile,
)

_CONSTRUCTION_TOKEN = object()


class FunctionNotFoundError(KeyError):
    """Raised when a function identifier is not present in a codebase."""


class SourceFileNotFoundError(KeyError):
    """Raised when a source identifier is not present in a codebase."""


class Codebase:
    """A queryable snapshot of functions and reliable direct calls."""

    def __init__(
        self,
        *,
        files: dict[str, SourceFile],
        analyzers: tuple[LanguageAnalyzer, ...],
        functions: dict[str, Function],
        calls: tuple[Call, ...],
        entry_points: tuple[EntryPoint, ...],
        diagnostics: tuple[Diagnostic, ...],
        _token: object | None = None,
    ) -> None:
        if _token is not _CONSTRUCTION_TOKEN:
            raise TypeError("Use Codebase.analyze() to construct a codebase.")
        self._files = files
        self._analyzers = analyzers
        self._functions = functions
        self._calls = calls
        self._entries = {
            (entry.function_id, entry.kind, entry.source): entry
            for entry in entry_points
        }
        self._diagnostics = diagnostics
        self._forward: dict[str, tuple[str, ...]] = {}
        self._reverse: dict[str, tuple[str, ...]] = {}
        self._functions_by_file: dict[str, tuple[Function, ...]] = {}
        self._build_indexes()

    @classmethod
    def analyze(
        cls,
        files: Sequence[SourceFile],
        analyzers: Sequence[LanguageAnalyzer],
    ) -> Codebase:
        unique_files: dict[str, SourceFile] = {}
        diagnostics: list[Diagnostic] = []
        for source_file in files:
            if source_file.source_id in unique_files:
                diagnostics.append(
                    Diagnostic(
                        code="duplicate_source_id",
                        severity="error",
                        message=f"More than one input source uses {source_file.source_id!r}.",
                        source_id=source_file.source_id,
                    )
                )
                continue
            unique_files[source_file.source_id] = source_file

        batches: dict[int, list[SourceFile]] = defaultdict(list)
        analyzer_list = tuple(analyzers)
        for source_file in unique_files.values():
            supported = [
                index
                for index, analyzer in enumerate(analyzer_list)
                if analyzer.supports(source_file)
            ]
            if not supported:
                diagnostics.append(
                    Diagnostic(
                        code="unsupported_file",
                        severity="error",
                        message=f"No analyzer supports {source_file.source_id!r}.",
                        source_id=source_file.source_id,
                    )
                )
            elif len(supported) > 1:
                diagnostics.append(
                    Diagnostic(
                        code="ambiguous_analyzer",
                        severity="error",
                        message=f"More than one analyzer supports {source_file.source_id!r}.",
                        source_id=source_file.source_id,
                    )
                )
            else:
                batches[supported[0]].append(source_file)

        analyses: list[AnalysisResult] = []
        for index, batch in sorted(batches.items()):
            analyses.append(analyzer_list[index].analyze(tuple(batch)))

        functions: dict[str, Function] = {}
        calls: list[Call] = []
        entry_points: list[EntryPoint] = []
        for analysis in analyses:
            diagnostics.extend(analysis.diagnostics)
            entry_points.extend(analysis.entry_points)
            for function in analysis.functions:
                if function.id in functions:
                    diagnostics.append(
                        Diagnostic(
                            code="duplicate_function_id",
                            severity="error",
                            message=f"More than one function uses {function.id!r}.",
                            source_id=function.source_id,
                            function_id=function.id,
                            line=function.source_range.start_line,
                        )
                    )
                else:
                    functions[function.id] = function
            calls.extend(analysis.calls)

        valid_calls: list[Call] = []
        for call in calls:
            if call.source_id not in functions:
                diagnostics.append(
                    Diagnostic(
                        code="unknown_call_source",
                        severity="error",
                        message=f"Call {call.name!r} has no indexed source function.",
                        function_id=call.source_id,
                        line=call.line,
                    )
                )
                continue
            if call.target_id is not None and call.target_id not in functions:
                diagnostics.append(
                    Diagnostic(
                        code="missing_call_target",
                        severity="warning",
                        message=(
                            f"Call {call.name!r} targets {call.target_id!r}, "
                            "which is not in this codebase."
                        ),
                        function_id=call.source_id,
                        line=call.line,
                    )
                )
            valid_calls.append(call)

        valid_entry_points: list[EntryPoint] = []
        for entry in entry_points:
            if entry.function_id not in functions:
                diagnostics.append(
                    Diagnostic(
                        code="missing_entry_point",
                        severity="error",
                        message=(
                            f"Entry point {entry.function_id!r} is not an indexed "
                            "function."
                        ),
                        function_id=entry.function_id,
                    )
                )
                continue
            valid_entry_points.append(entry)

        return cls(
            files=unique_files,
            analyzers=analyzer_list,
            functions=functions,
            calls=tuple(
                sorted(
                    valid_calls,
                    key=lambda call: (
                        call.source_id,
                        call.line,
                        call.name.casefold(),
                        call.target_id or "",
                    ),
                )
            ),
            entry_points=tuple(
                sorted(
                    (
                        entry for entry in valid_entry_points
                    ),
                    key=lambda entry: (
                        entry.function_id,
                        entry.kind,
                        entry.source,
                    ),
                ),
            ),
            diagnostics=tuple(
                sorted(
                    diagnostics,
                    key=lambda diagnostic: (
                        diagnostic.source_id or "",
                        diagnostic.line or 0,
                        diagnostic.code,
                        diagnostic.function_id or "",
                    ),
                )
            ),
            _token=_CONSTRUCTION_TOKEN,
        )

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        return self._diagnostics

    @property
    def entry_points(self) -> tuple[EntryPoint, ...]:
        return tuple(
            sorted(
                self._entries.values(),
                key=lambda entry: (entry.function_id, entry.kind, entry.source),
            )
        )

    @property
    def functions(self) -> tuple[Function, ...]:
        return tuple(self._functions[function_id] for function_id in sorted(self._functions))

    @property
    def source_files(self) -> tuple[SourceFile, ...]:
        return tuple(self._files[path] for path in sorted(self._files))

    def find_source_files(
        self,
        *,
        source_id_prefix: str | None = None,
        language: str | None = None,
        extension: str | None = None,
    ) -> tuple[SourceFile, ...]:
        normalized_language = language.casefold() if language is not None else None
        normalized_extension = extension.casefold() if extension is not None else None
        return tuple(
            source_file
            for source_file in self.source_files
            if (
                source_id_prefix is None
                or source_file.source_id.startswith(source_id_prefix)
            )
            and (
                normalized_language is None
                or (source_file.language or "").casefold() == normalized_language
                or (
                    source_file.language is None
                    and source_file.source_id.rpartition(".")[2].casefold()
                    == normalized_language.lstrip(".")
                )
            )
            and (
                normalized_extension is None
                or source_file.source_id.casefold().endswith(
                    normalized_extension
                    if normalized_extension.startswith(".")
                    else "." + normalized_extension
                )
            )
        )

    def source_file(self, source_id: str) -> SourceFile:
        try:
            return self._files[str(source_id)]
        except KeyError as error:
            raise SourceFileNotFoundError(source_id) from error

    def functions_in_file(self, source_id: str) -> tuple[Function, ...]:
        self.source_file(source_id)
        return self._functions_by_file[str(source_id)]

    def find_functions(
        self,
        *,
        name: str | None = None,
        qualified_name: str | None = None,
        source_id: str | None = None,
        language: str | None = None,
        module: str | None = None,
        attribute: tuple[str, object] | None = None,
    ) -> tuple[Function, ...]:
        return tuple(
            function
            for function in self.functions
            if (name is None or function.name == name)
            and (
                qualified_name is None
                or function.qualified_name == qualified_name
            )
            and (source_id is None or function.source_id == source_id)
            and (language is None or function.language.casefold() == language.casefold())
            and (module is None or function.module == module)
            and (
                attribute is None
                or function.attributes.get(attribute[0]) == attribute[1]
            )
        )

    def functions_at(self, source_id: str, line: int) -> tuple[Function, ...]:
        return tuple(
            function
            for function in self.functions_in_file(source_id)
            if function.source_range.start_line <= line <= function.source_range.end_line
        )

    def function(self, function_id: str) -> Function:
        try:
            return self._functions[str(function_id)]
        except KeyError as error:
            raise FunctionNotFoundError(function_id) from error

    def get_function(self, function_id: str) -> Function | None:
        return self._functions.get(str(function_id))

    def calls_from(
        self, function_id: str, *, resolution: str | None = None
    ) -> tuple[Call, ...]:
        self.function(function_id)
        if resolution not in {None, "resolved", "unresolved"}:
            raise ValueError("resolution must be 'resolved', 'unresolved', or None")
        return tuple(
            call
            for call in self._calls
            if call.source_id == function_id
            and (
                resolution is None
                or (resolution == "resolved" and call.target_id is not None)
                or (resolution == "unresolved" and call.target_id is None)
            )
        )

    def calls_to(self, function_id: str) -> tuple[Call, ...]:
        self.function(function_id)
        return tuple(call for call in self._calls if call.target_id == function_id)

    def callees(self, function_id: str, *, transitive: bool = False) -> tuple[Function, ...]:
        return self._related(function_id, self._forward, transitive)

    def callers(self, function_id: str, *, transitive: bool = False) -> tuple[Function, ...]:
        return self._related(function_id, self._reverse, transitive)

    def dependencies_of(self, function_id: str) -> tuple[Function, ...]:
        return self.callees(function_id)

    def dependents_of(self, function_id: str) -> tuple[Function, ...]:
        return self.callers(function_id)

    def descendants_of(self, function_id: str) -> tuple[Function, ...]:
        return self.callees(function_id, transitive=True)

    def ancestors_of(self, function_id: str) -> tuple[Function, ...]:
        return self.callers(function_id, transitive=True)

    def dependency_context(
        self, entry_id: str, *, limits: ContextLimits | None = None
    ) -> AnalysisContext:
        self.function(entry_id)
        limits = limits or ContextLimits()
        if limits.max_depth is not None and limits.max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        if limits.max_functions is not None and limits.max_functions < 1:
            raise ValueError("max_functions must be positive")
        if limits.max_source_chars is not None and limits.max_source_chars < 0:
            raise ValueError("max_source_chars must be non-negative")
        entry = next(
            (item for item in self.entry_points if item.function_id == entry_id),
            EntryPoint(function_id=entry_id, kind="root", source="query"),
        )
        paths = self._paths_from(entry_id, max_depth=limits.max_depth)
        reachable = tuple(path[-1] for path in paths)
        reasons: list[str] = []
        if limits.max_functions is not None and len(reachable) > limits.max_functions:
            reachable = reachable[: limits.max_functions]
            reasons.append("max_functions")
        if limits.max_source_chars is not None:
            selected: list[str] = []
            chars = 0
            for function_id in reachable:
                size = len(self._functions[function_id].source)
                if chars + size > limits.max_source_chars:
                    break
                selected.append(function_id)
                chars += size
            if len(selected) < len(reachable):
                reasons.append("max_source_chars")
            reachable = tuple(selected)
        reachable_ids = set(reachable)
        calls = tuple(
            call
            for call in self._calls
            if call.source_id in reachable_ids
            and call.target_id in reachable_ids
            and call.target_id in self._functions
        )
        diagnostics = tuple(
            diagnostic
            for diagnostic in self._diagnostics
            if diagnostic.function_id in reachable_ids
        )
        return AnalysisContext(
            entry=entry,
            functions=tuple(self._functions[function_id] for function_id in reachable),
            calls=calls,
            paths=tuple(path for path in paths if path[-1] in reachable_ids),
            diagnostics=diagnostics,
            truncated=bool(reasons),
            truncation_reasons=tuple(reasons),
        )

    def _build_indexes(self) -> None:
        functions_by_file: dict[str, list[Function]] = {
            path: [] for path in self._files
        }
        for function in self._functions.values():
            functions_by_file.setdefault(function.source_id, []).append(function)
        self._functions_by_file = {
            path: tuple(sorted(functions, key=lambda function: function.id))
            for path, functions in functions_by_file.items()
        }

        forward: dict[str, set[str]] = defaultdict(set)
        reverse: dict[str, set[str]] = defaultdict(set)
        for call in self._calls:
            if (
                call.target_id is not None
                and call.source_id in self._functions
                and call.target_id in self._functions
            ):
                forward[call.source_id].add(call.target_id)
                reverse[call.target_id].add(call.source_id)
        self._forward = {
            source_id: tuple(sorted(target_ids))
            for source_id, target_ids in forward.items()
        }
        self._reverse = {
            target_id: tuple(sorted(source_ids))
            for target_id, source_ids in reverse.items()
        }

    def _related(
        self,
        function_id: str,
        index: dict[str, tuple[str, ...]],
        transitive: bool,
    ) -> tuple[Function, ...]:
        self.function(function_id)
        if not transitive:
            return tuple(
                self._functions[related_id]
                for related_id in index.get(function_id, ())
                if related_id != function_id
            )
        seen = {function_id}
        pending = deque(index.get(function_id, ()))
        related: list[str] = []
        while pending:
            related_id = pending.popleft()
            if related_id in seen:
                continue
            seen.add(related_id)
            related.append(related_id)
            pending.extend(index.get(related_id, ()))
        return tuple(self._functions[related_id] for related_id in related)

    def _paths_from(
        self, function_id: str, *, max_depth: int | None = None
    ) -> tuple[tuple[str, ...], ...]:
        paths: dict[str, tuple[str, ...]] = {function_id: (function_id,)}
        pending = deque([function_id])
        while pending:
            source_id = pending.popleft()
            if max_depth is not None and len(paths[source_id]) - 1 >= max_depth:
                continue
            for target_id in self._forward.get(source_id, ()):
                if target_id not in paths:
                    paths[target_id] = (*paths[source_id], target_id)
                    pending.append(target_id)
        return tuple(paths[function_id] for function_id in sorted(paths))
