from __future__ import annotations

from collections import defaultdict, deque
from typing import Callable, Iterable, Sequence

from .frontend import LanguageFrontend
from .model import (
    AnalysisContext,
    Call,
    Diagnostic,
    EntryCandidate,
    EntryPoint,
    FileAnalysis,
    Function,
    RefreshResult,
    SourceFile,
)


class FunctionNotFoundError(KeyError):
    """Raised when a function identifier is not present in a codebase."""


class Codebase:
    """A queryable snapshot of functions and reliable direct calls."""

    def __init__(
        self,
        *,
        files: dict[str, SourceFile],
        frontends: tuple[LanguageFrontend, ...],
        functions: dict[str, Function],
        calls: tuple[Call, ...],
        entry_candidates: tuple[EntryCandidate, ...],
        diagnostics: tuple[Diagnostic, ...],
    ) -> None:
        self._files = files
        self._frontends = frontends
        self._functions = functions
        self._calls = calls
        self._entry_candidates = entry_candidates
        self._diagnostics = diagnostics
        self._entries: dict[tuple[str, str, str], EntryPoint] = {}
        self._forward: dict[str, tuple[str, ...]] = {}
        self._reverse: dict[str, tuple[str, ...]] = {}
        self._build_indexes()

    @classmethod
    def analyze(
        cls,
        files: Sequence[SourceFile],
        frontends: Sequence[LanguageFrontend],
    ) -> Codebase:
        unique_files: dict[str, SourceFile] = {}
        diagnostics: list[Diagnostic] = []
        for source_file in files:
            if source_file.path in unique_files:
                diagnostics.append(
                    Diagnostic(
                        code="duplicate_file_path",
                        severity="error",
                        message=f"More than one input file uses {source_file.path!r}.",
                        path=source_file.path,
                    )
                )
                continue
            unique_files[source_file.path] = source_file

        batches: dict[int, list[SourceFile]] = defaultdict(list)
        frontend_list = tuple(frontends)
        for source_file in unique_files.values():
            supported = [
                index
                for index, frontend in enumerate(frontend_list)
                if frontend.supports(source_file)
            ]
            if not supported:
                diagnostics.append(
                    Diagnostic(
                        code="unsupported_file",
                        severity="error",
                        message=f"No frontend supports {source_file.path!r}.",
                        path=source_file.path,
                    )
                )
            elif len(supported) > 1:
                diagnostics.append(
                    Diagnostic(
                        code="ambiguous_frontend",
                        severity="error",
                        message=f"More than one frontend supports {source_file.path!r}.",
                        path=source_file.path,
                    )
                )
            else:
                batches[supported[0]].append(source_file)

        analyses: list[FileAnalysis] = []
        for index, batch in sorted(batches.items()):
            analyses.append(frontend_list[index].analyze(tuple(batch)))

        functions: dict[str, Function] = {}
        calls: list[Call] = []
        candidates: list[EntryCandidate] = []
        for analysis in analyses:
            diagnostics.extend(analysis.diagnostics)
            candidates.extend(analysis.entry_candidates)
            for function in analysis.functions:
                if function.id in functions:
                    diagnostics.append(
                        Diagnostic(
                            code="duplicate_function_id",
                            severity="error",
                            message=f"More than one function uses {function.id!r}.",
                            path=function.file,
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

        return cls(
            files=unique_files,
            frontends=frontend_list,
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
            entry_candidates=tuple(
                sorted(
                    candidates,
                    key=lambda candidate: (
                        candidate.function_id,
                        candidate.kind,
                        candidate.source,
                    ),
                )
            ),
            diagnostics=tuple(
                sorted(
                    diagnostics,
                    key=lambda diagnostic: (
                        diagnostic.path or "",
                        diagnostic.line or 0,
                        diagnostic.code,
                        diagnostic.function_id or "",
                    ),
                )
            ),
        )

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        return self._diagnostics

    @property
    def entry_candidates(self) -> tuple[EntryCandidate, ...]:
        return self._entry_candidates

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

    def function(self, function_id: str) -> Function:
        try:
            return self._functions[str(function_id)]
        except KeyError as error:
            raise FunctionNotFoundError(function_id) from error

    def get_function(self, function_id: str) -> Function | None:
        return self._functions.get(str(function_id))

    def calls_from(self, function_id: str) -> tuple[Call, ...]:
        self.function(function_id)
        return tuple(call for call in self._calls if call.source_id == function_id)

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

    def accept_entry_candidates(self, *, kind: str | None = None) -> None:
        for candidate in self._entry_candidates:
            self._add_entry(
                candidate.function_id,
                kind=kind or candidate.kind,
                source=candidate.source,
            )

    def mark_entries(
        self, predicate: Callable[[Function], bool], *, kind: str
    ) -> None:
        for function in self.functions:
            if predicate(function):
                self._add_entry(function.id, kind=kind, source="predicate")

    def set_entries(self, function_ids: Iterable[str], *, kind: str) -> None:
        self._entries.clear()
        for function_id in function_ids:
            self._add_entry(str(function_id), kind=kind, source="manual")

    def mark_hinted_entry_points(self) -> None:
        self.accept_entry_candidates()

    def mark_entry_points(
        self, predicate: Callable[[Function], bool], *, kind: str
    ) -> None:
        self.mark_entries(predicate, kind=kind)

    def set_entry_points(self, function_ids: Iterable[str], *, kind: str) -> None:
        self.set_entries(function_ids, kind=kind)

    def context_for(self, entry_id: str) -> AnalysisContext:
        self.function(entry_id)
        entry = next(
            (item for item in self.entry_points if item.function_id == entry_id),
            EntryPoint(function_id=entry_id, kind="unconfirmed", source="query"),
        )
        paths = self._paths_from(entry_id)
        reachable = tuple(path[-1] for path in paths)
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
            paths=paths,
            diagnostics=diagnostics,
        )

    def refresh(
        self,
        changed_files: Sequence[SourceFile] = (),
        *,
        removed_paths: Sequence[str] = (),
    ) -> RefreshResult:
        changed_paths = {source_file.path for source_file in changed_files}
        removed = set(removed_paths)
        overlap = changed_paths & removed
        if overlap:
            raise ValueError(
                "A path cannot be changed and removed in one refresh: "
                + ", ".join(sorted(overlap))
            )

        old_functions = self._functions
        old_edges = self._edge_set()
        old_reverse = self._reverse
        old_entries = self.entry_points
        new_files = dict(self._files)
        for path in removed:
            new_files.pop(path, None)
        for source_file in changed_files:
            new_files[source_file.path] = source_file
        rebuilt = self.analyze(tuple(new_files.values()), self._frontends)

        new_edges = rebuilt._edge_set()
        changed_function_ids = {
            function_id
            for function_id in set(old_functions) | set(rebuilt._functions)
            if old_functions.get(function_id) != rebuilt._functions.get(function_id)
        }
        changed_call_sources = {
            source_id
            for source_id in set(old_functions) | set(rebuilt._functions)
            if {
                edge for edge in old_edges if edge[0] == source_id
            }
            != {
                edge for edge in new_edges if edge[0] == source_id
            }
        }
        seeds = changed_function_ids | changed_call_sources
        affected_ids = self._ancestors_in(seeds, old_reverse) | self._ancestors_in(
            seeds, rebuilt._reverse
        )
        affected_entries = tuple(
            entry
            for entry in old_entries
            if entry.function_id in affected_ids
        )

        rebuilt._entries = {
            key: entry
            for key, entry in self._entries.items()
            if entry.function_id in rebuilt._functions
        }
        self.__dict__.update(rebuilt.__dict__)
        return RefreshResult(
            changed_function_ids=tuple(sorted(changed_function_ids)),
            changed_call_source_ids=tuple(sorted(changed_call_sources)),
            affected_entry_points=affected_entries,
            diagnostics=self._diagnostics,
        )

    def _build_indexes(self) -> None:
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

    def _paths_from(self, function_id: str) -> tuple[tuple[str, ...], ...]:
        paths: dict[str, tuple[str, ...]] = {function_id: (function_id,)}
        pending = deque([function_id])
        while pending:
            source_id = pending.popleft()
            for target_id in self._forward.get(source_id, ()):
                if target_id not in paths:
                    paths[target_id] = (*paths[source_id], target_id)
                    pending.append(target_id)
        return tuple(paths[function_id] for function_id in sorted(paths))

    def _add_entry(self, function_id: str, *, kind: str, source: str) -> None:
        self.function(function_id)
        entry = EntryPoint(function_id=function_id, kind=kind, source=source)
        self._entries[(function_id, kind, source)] = entry

    def _edge_set(self) -> set[tuple[str, str]]:
        return {
            (call.source_id, call.target_id)
            for call in self._calls
            if call.target_id is not None
            and call.source_id in self._functions
            and call.target_id in self._functions
        }

    @staticmethod
    def _ancestors_in(
        seeds: Iterable[str], index: dict[str, tuple[str, ...]]
    ) -> set[str]:
        affected = set(seeds)
        pending = deque(seeds)
        while pending:
            function_id = pending.popleft()
            for caller_id in index.get(function_id, ()):
                if caller_id not in affected:
                    affected.add(caller_id)
                    pending.append(caller_id)
        return affected
