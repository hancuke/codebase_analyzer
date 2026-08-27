from __future__ import annotations

import re
from pathlib import PurePath
from typing import Sequence

from .model import (
    Call,
    Diagnostic,
    EntryCandidate,
    FileAnalysis,
    Function,
    SourceFile,
    SourceRange,
)


_PROCEDURE_START = re.compile(
    r"^\s*(?:(Public|Private|Friend|Static)\s+)?(?:Sub|Function)\s+"
    r"([A-Za-z_]\w*)\b",
    re.IGNORECASE,
)
_PROCEDURE_END = re.compile(r"^\s*End\s+(?:Sub|Function)\b", re.IGNORECASE)
_EXPLICIT_CALL = re.compile(
    r"\bCall\s+([A-Za-z_]\w*)(?!\s*\.)", re.IGNORECASE
)
_PAREN_CALL = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\s*\(", re.IGNORECASE)
_BARE_CALL = re.compile(r"^\s*(?:Call\s+)?([A-Za-z_]\w*)\b", re.IGNORECASE)
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


class VbaFrontend:
    """A deliberately small VBA frontend for Sub and Function procedures."""

    def supports(self, file: SourceFile) -> bool:
        return file.language == "vba" or (
            file.language is None
            and PurePath(file.path).suffix.casefold() in {".bas", ".cls", ".frm"}
        )

    def analyze(self, files: Sequence[SourceFile]) -> FileAnalysis:
        functions: list[Function] = []
        diagnostics: list[Diagnostic] = []
        for source_file in files:
            found, file_diagnostics = self._functions_in(source_file)
            functions.extend(found)
            diagnostics.extend(file_diagnostics)

        by_name: dict[str, list[Function]] = {}
        for function in functions:
            by_name.setdefault(function.name.casefold(), []).append(function)

        calls: list[Call] = []
        candidates: list[EntryCandidate] = []
        for function in functions:
            if function.name.casefold().endswith("_click"):
                candidates.append(
                    EntryCandidate(function_id=function.id, kind="form_event")
                )
            for name, line, column in self._calls_in(function):
                targets = by_name.get(name.casefold(), [])
                if len(targets) == 1:
                    calls.append(
                        Call(
                            source_id=function.id,
                            name=name,
                            line=line,
                            column=column,
                            target_id=targets[0].id,
                            evidence="vba_name_resolution",
                        )
                    )
                else:
                    reason = (
                        "no indexed VBA procedure has that name"
                        if not targets
                        else "more than one indexed VBA procedure has that name"
                    )
                    diagnostics.append(
                        Diagnostic(
                            code="unresolved_call",
                            severity="warning",
                            message=f"Cannot resolve VBA call {name!r}: {reason}.",
                            path=function.file,
                            function_id=function.id,
                            line=line,
                        )
                    )
                    calls.append(
                        Call(
                            source_id=function.id,
                            name=name,
                            line=line,
                            column=column,
                        )
                    )
        return FileAnalysis(
            functions=tuple(functions),
            calls=tuple(calls),
            entry_candidates=tuple(candidates),
            diagnostics=tuple(diagnostics),
        )

    def _functions_in(
        self, source_file: SourceFile
    ) -> tuple[list[Function], list[Diagnostic]]:
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
                    path=source_file.path,
                    line=start + 1,
                )
            )
        return functions, diagnostics

    @staticmethod
    def _function_from_lines(
        source_file: SourceFile,
        lines: list[str],
        start: int,
        end: int,
        match: re.Match[str],
    ) -> Function:
        module = PurePath(source_file.path).stem
        name = match.group(2)
        visibility = (match.group(1) or "public").casefold()
        return Function(
            id=f"vba:{module}:{name}",
            name=name,
            language="vba",
            module=module,
            file=source_file.path,
            source="".join(lines[start : end + 1]),
            source_range=SourceRange(start_line=start + 1, end_line=end + 1),
            attributes={"visibility": visibility},
        )

    @staticmethod
    def _calls_in(function: Function) -> list[tuple[str, int, int]]:
        calls: list[tuple[str, int, int]] = []
        start_line = function.source_range.start_line
        lines = function.source.splitlines()
        for offset, line in enumerate(lines[1:], start=1):
            code = line.split("'", maxsplit=1)[0]
            found: set[tuple[str, int]] = set()
            for pattern in (_EXPLICIT_CALL, _PAREN_CALL, _BARE_CALL):
                for match in pattern.finditer(code):
                    name = match.group(1)
                    trailing = code[match.end(1) :].lstrip()
                    if name.casefold() in _CONTROL_WORDS or trailing.startswith("="):
                        continue
                    position = (name.casefold(), match.start(1))
                    if position not in found:
                        found.add(position)
                        calls.append((name, start_line + offset, match.start(1) + 1))
        return calls
