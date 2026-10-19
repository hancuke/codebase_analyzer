from __future__ import annotations

import re
from dataclasses import replace
from functools import lru_cache
from pathlib import PurePath
from typing import Sequence

from pygments.lexers import get_lexer_by_name
from pygments.token import Comment, Name, String, Text

from .analyzer import BaseAnalyzer, RawCall
from .model import (
    AnalysisResult,
    Diagnostic,
    EntryPoint,
    Function,
    SourceFile,
    SourceRange,
)
from .vba_constants import attach_constants


_PROCEDURE_START = re.compile(
    r"^\s*(?:(Public|Private|Friend|Static)\s+)?(?:Sub|Function)\s+"
    r"([A-Za-z_]\w*)\b",
    re.IGNORECASE,
)
_PROCEDURE_END = re.compile(r"^\s*End\s+(?:Sub|Function)\b", re.IGNORECASE)
_CONTROL_WORDS = {
    "call",
    "debug",
    "dim",
    "end",
    "exit",
    "if",
    "for",
    "let",
    "while",
    "do",
    "loop",
    "on",
    "option",
    "set",
    "select",
    "function",
    "sub",
    "property",
}


class VbaAnalyzer(BaseAnalyzer):
    """Analyze VBA with optional instance-local, bounded syntax caches."""

    language_name = "vba"
    file_extensions = {".bas", ".cls", ".frm"}
    is_case_sensitive = False

    def __init__(self, *, cache_size: int = 0) -> None:
        """Limit each syntax cache to cache_size entries; zero disables reuse."""
        if isinstance(cache_size, bool) or not isinstance(cache_size, int):
            raise TypeError("cache_size must be a non-negative integer")
        if cache_size < 0:
            raise ValueError("cache_size must be a non-negative integer")
        self._cached_functions = lru_cache(maxsize=cache_size)(self._parse_functions)
        self._cached_raw_calls = lru_cache(maxsize=cache_size)(self._parse_raw_calls)

    def clear_cache(self) -> None:
        """Release cached syntax facts without changing existing snapshots."""
        self._cached_functions.cache_clear()
        self._cached_raw_calls.cache_clear()

    def analyze(self, files: Sequence[SourceFile]) -> AnalysisResult:
        result = super().analyze(files)
        functions, diagnostics = attach_constants(files, result.functions)
        return replace(
            result, functions=functions,
            diagnostics=result.diagnostics + diagnostics,
        )

    def extract_functions(
        self, source_file: SourceFile
    ) -> tuple[list[Function], list[Diagnostic]]:
        functions, diagnostics = self._cached_functions(source_file)
        return list(functions), list(diagnostics)

    def _parse_functions(
        self, source_file: SourceFile
    ) -> tuple[tuple[Function, ...], tuple[Diagnostic, ...]]:
        lines = source_file.content.splitlines(keepends=True)
        functions: list[Function] = []
        diagnostics: list[Diagnostic] = []
        start: int | None = None
        match: re.Match[str] | None = None
        for index, line in enumerate(lines):
            if start is None:
                candidate = _PROCEDURE_START.match(line)
                if candidate:
                    start = index
                    match = candidate
                continue
            if _PROCEDURE_END.match(line):
                assert match is not None
                functions.append(
                    self._function_from_lines(source_file, lines, start, index, match)
                )
                start = None
                match = None
        if start is not None:
            assert match is not None
            diagnostics.append(
                Diagnostic(
                    code="unterminated_procedure",
                    severity="error",
                    message="VBA procedure has no matching End Sub or End Function.",
                    source_id=source_file.source_id,
                    line=start + 1,
                )
            )
        return tuple(functions), tuple(diagnostics)

    def extract_raw_calls(self, function: Function) -> list[RawCall]:
        """Return fresh raw calls, reusing only context-independent syntax."""
        return [
            RawCall(name=name, line=line)
            for name, line in self._cached_raw_calls(
                function.source, function.source_range.start_line
            )
        ]

    def _parse_raw_calls(
        self, source: str, start_line: int
    ) -> tuple[tuple[str, int], ...]:
        """Extract likely VBA calls from Pygments tokens.

        Pygments handles comments and strings as separate token types. This
        method only applies lightweight call-position rules; name resolution
        remains the responsibility of BaseAnalyzer.
        """
        calls: list[tuple[str, int]] = []
        lexer = get_lexer_by_name("vb.net")
        token_stream = []
        position = 0
        offset = 0
        for token_position, token_type, value in lexer.get_tokens_unprocessed(
            source
        ):
            offset += source.count("\n", position, token_position)
            token_stream.append((token_type, value, offset))
            position = token_position
        significant = [
            item
            for item in token_stream
            if not item[0] in Text.Whitespace
            and not item[0] in Comment
            and not item[0] in String
        ]
        seen: set[tuple[str, int]] = set()

        for index, (token_type, value, offset) in enumerate(significant):
            if token_type not in Name or not re.fullmatch(r"[A-Za-z_]\w*", value):
                continue

            line = start_line + offset
            if line == start_line:
                continue

            normalized = value.casefold()
            if normalized in _CONTROL_WORDS:
                continue

            previous = significant[index - 1] if index else None
            next_token = (
                significant[index + 1] if index + 1 < len(significant) else None
            )
            previous_same_line = previous is not None and previous[2] == offset
            next_value = next_token[1] if next_token is not None else ""
            previous_value = previous[1] if previous_same_line else ""

            if next_value == "=" or previous_value == ".":
                continue

            is_call = (
                next_value == "("
                or previous_value.casefold() == "call"
                or not previous_same_line
            )
            if not is_call:
                continue

            key = (normalized, line)
            if key in seen:
                continue
            seen.add(key)
            calls.append((value, line))
        return tuple(calls)

    def detect_entry_point(self, function: Function) -> EntryPoint | None:
        if function.name.casefold().endswith("_click"):
            return EntryPoint(
                function_id=function.id, kind="form_event", source="analyzer"
            )
        return None

    @staticmethod
    def _function_from_lines(
        source_file: SourceFile,
        lines: list[str],
        start: int,
        end: int,
        match: re.Match[str],
    ) -> Function:
        module = PurePath(source_file.source_id).stem
        name = match.group(2)
        visibility = (match.group(1) or "public").casefold()
        return Function(
            id=f"vba:{module}:{name}",
            name=name,
            language="vba",
            module=module,
            source_id=source_file.source_id,
            source="".join(lines[start : end + 1]),
            source_range=SourceRange(start_line=start + 1, end_line=end + 1),
            attributes={"visibility": visibility},
        )
