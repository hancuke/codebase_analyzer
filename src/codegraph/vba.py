from __future__ import annotations

import re
from pathlib import PurePath

from pygments.lexers import get_lexer_by_name
from pygments.token import Comment, Name, String, Text

from .frontend import BaseFrontend, RawCall
from .model import (
    Diagnostic,
    EntryCandidate,
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


class VbaFrontend(BaseFrontend):
    """A VBA frontend built on the standard BaseFrontend pipeline."""

    language_name = "vba"
    file_extensions = {".bas", ".cls", ".frm"}
    is_case_sensitive = False

    def extract_functions(
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

    def extract_raw_calls(self, function: Function) -> list[RawCall]:
        """Extract likely VBA calls from Pygments tokens.

        Pygments handles comments and strings as separate token types. This
        method only applies lightweight call-position rules; name resolution
        remains the responsibility of BaseFrontend.
        """
        calls: list[RawCall] = []
        lexer = get_lexer_by_name("vb.net")
        token_stream = [
            (token_type, value, function.source[:position].count("\n"))
            for position, token_type, value in lexer.get_tokens_unprocessed(
                function.source
            )
        ]
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

            line = function.source_range.start_line + offset
            if line == function.source_range.start_line:
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
            calls.append(RawCall(name=value, line=line))
        return calls

    def detect_entry_candidate(self, function: Function) -> EntryCandidate | None:
        if function.name.casefold().endswith("_click"):
            return EntryCandidate(function_id=function.id, kind="form_event")
        return None

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
