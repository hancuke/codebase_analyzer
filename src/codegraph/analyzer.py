from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePath
from typing import Mapping, Protocol, Sequence

from .model import (
    Call,
    Diagnostic,
    AnalysisResult,
    EntryPoint,
    Function,
    SourceFile,
)


class LanguageAnalyzer(Protocol):
    def supports(self, file: SourceFile) -> bool:
        """Return whether this analyzer owns the source."""

    def analyze(self, files: Sequence[SourceFile]) -> AnalysisResult:
        """Analyze a batch of sources supported by this analyzer."""


@dataclass(frozen=True)
class RawCall:
    """An un-resolved call site extracted from source code."""

    name: str
    line: int
    context: Mapping[str, object] = field(default_factory=dict)


class BaseAnalyzer:
    """Template-based analyzer base class enforcing a standard analysis pipeline.

    New language analyzers can inherit from this class to eliminate boilerplate code.
    Subclasses only need to implement:
      - language_name (e.g. "vba", "python", "java")
      - file_extensions (e.g. {".bas", ".cls", ".frm"})
      - extract_functions(source_file) -> (functions, diagnostics)
      - extract_raw_calls(function) -> list[RawCall]
      - (optional) is_case_sensitive: bool (default True)
      - (optional) detect_entry_point(function) -> EntryPoint | None
      - (optional) resolve_target(raw_call, source_function, symbol_index) -> str | None
    """

    language_name: str = ""
    file_extensions: set[str] = set()
    is_case_sensitive: bool = True

    def supports(self, file: SourceFile) -> bool:
        if file.language is not None:
            return file.language.casefold() == self.language_name.casefold()
        if not file.source_id:
            return False
        ext = PurePath(file.source_id).suffix
        if not self.is_case_sensitive:
            ext = ext.casefold()
        valid_exts = (
            self.file_extensions
            if self.is_case_sensitive
            else {e.casefold() for e in self.file_extensions}
        )
        return ext in valid_exts

    def normalize_name(self, name: str) -> str:
        return name if self.is_case_sensitive else name.casefold()

    def analyze(self, files: Sequence[SourceFile]) -> AnalysisResult:
        all_functions: list[Function] = []
        all_diagnostics: list[Diagnostic] = []

        # Step 1: Extract functions and file-level diagnostics
        for source_file in files:
            funcs, diags = self.extract_functions(source_file)
            all_functions.extend(funcs)
            all_diagnostics.extend(diags)

        # Step 2: Build symbol index by normalized name
        symbol_index: dict[str, list[Function]] = {}
        for function in all_functions:
            norm_name = self.normalize_name(function.name)
            symbol_index.setdefault(norm_name, []).append(function)

        # Process each function for entry points and calls.
        all_calls: list[Call] = []
        all_entry_points: list[EntryPoint] = []

        for function in all_functions:
            entry_point = self.detect_entry_point(function)
            if entry_point is not None:
                all_entry_points.append(entry_point)

            # Step 3: Raw call extraction
            raw_calls = self.extract_raw_calls(function)
            for raw_call in raw_calls:
                # Step 4: Resolve call target and collect diagnostic if unresolved
                target_id, diag = self._resolve_and_diagnose(
                    raw_call, function, symbol_index
                )
                if diag is not None:
                    all_diagnostics.append(diag)
                evidence = (
                    f"{self.language_name}_name_resolution"
                    if target_id is not None
                    else None
                )
                all_calls.append(
                    Call(
                        source_id=function.id,
                        name=raw_call.name,
                        line=raw_call.line,
                        target_id=target_id,
                        evidence=evidence,
                    )
                )

        return AnalysisResult(
            functions=tuple(all_functions),
            calls=tuple(all_calls),
            entry_points=tuple(all_entry_points),
            diagnostics=tuple(all_diagnostics),
        )

    def extract_functions(
        self, source_file: SourceFile
    ) -> tuple[list[Function], list[Diagnostic]]:
        """Extract functions/procedures and syntax diagnostics from a single file."""
        raise NotImplementedError

    def extract_raw_calls(self, function: Function) -> list[RawCall]:
        """Extract un-resolved raw call sites from a function's source code."""
        raise NotImplementedError

    def detect_entry_point(self, function: Function) -> EntryPoint | None:
        """Hook to detect a structural entry point."""
        return None

    def resolve_target(
        self,
        raw_call: RawCall,
        source_function: Function,
        symbol_index: dict[str, list[Function]],
    ) -> str | None | tuple[str | None, str | None]:
        """Custom target resolution hook.

        Default implementation looks up symbol_index by normalized call name:
        - 1 match: returns target function ID
        - 0 or >1 matches: returns None
        """
        norm_name = self.normalize_name(raw_call.name)
        candidates = symbol_index.get(norm_name, [])
        if len(candidates) == 1:
            return candidates[0].id
        return None

    def _resolve_and_diagnose(
        self,
        raw_call: RawCall,
        source_function: Function,
        symbol_index: dict[str, list[Function]],
    ) -> tuple[str | None, Diagnostic | None]:
        res = self.resolve_target(raw_call, source_function, symbol_index)
        if isinstance(res, tuple):
            target_id, custom_reason = res
        else:
            target_id = res
            custom_reason = None

        if target_id is not None:
            return target_id, None

        norm_name = self.normalize_name(raw_call.name)
        candidates = symbol_index.get(norm_name, [])
        lang_upper = self.language_name.upper() if self.language_name else "language"
        if custom_reason:
            reason = custom_reason
        elif not candidates:
            reason = f"no indexed {lang_upper} procedure has that name"
        else:
            reason = f"more than one indexed {lang_upper} procedure has that name"

        diag = Diagnostic(
            code="unresolved_call",
            severity="warning",
            message=f"Cannot resolve {lang_upper} call {raw_call.name!r}: {reason}.",
            source_id=source_function.source_id,
            function_id=source_function.id,
            line=raw_call.line,
        )
        return None, diag
