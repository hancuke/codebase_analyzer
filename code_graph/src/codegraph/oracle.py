from __future__ import annotations

import re
from pathlib import PurePath
from typing import Sequence

from pygments.lexers import get_lexer_by_name
from pygments.token import Comment, Name, String, Text

from .analyzer import BaseAnalyzer, RawCall
from .model import AnalysisResult, Diagnostic, Function, SourceFile, SourceRange


_PACKAGE = re.compile(
    r"\bcreate\s+(?:or\s+replace\s+)?package\s+(body\s+)?"
    r"(?:(?:[A-Za-z_]\w*)\.)?([A-Za-z_]\w*)",
    re.IGNORECASE,
)
_MEMBER = re.compile(
    r"^\s*(?:(?:public|private|protected)\s+)?"
    r"(procedure\s+([A-Za-z_]\w*)|function\s+([A-Za-z_]\w*))"
    r"(?P<tail>.*)$",
    re.IGNORECASE,
)
_IMPLEMENTATION = re.compile(r"\b(?:is|as)\b", re.IGNORECASE)
_BARE_END = re.compile(r"^\s*end\s*;", re.IGNORECASE)
_CONTROL_WORDS = {
    "and", "as", "begin", "case", "close", "commit", "constant", "cursor",
    "declare", "delete", "else", "elsif", "end", "exception", "exit", "for",
    "function", "if", "in", "insert", "into", "is", "loop", "not", "null",
    "open", "or", "out", "package", "procedure", "raise", "return", "rollback",
    "select", "then", "true", "update", "values", "when", "while", "with",
}


class OraclePlsqlAnalyzer(BaseAnalyzer):
    """Parser for Oracle PL/SQL package members and their direct calls."""

    language_name = "plsql"
    file_extensions = {".sql", ".pks", ".pkb", ".pls"}
    is_case_sensitive = False

    def __init__(self) -> None:
        self._functions: tuple[Function, ...] = ()

    def analyze(self, files: Sequence[SourceFile]) -> AnalysisResult:
        body_packages = {
            match.group(2).casefold()
            for source_file in files
            for match in [_PACKAGE.search(source_file.content)]
            if match is not None and match.group(1) is not None
        }
        analysis_files = tuple(
            source_file
            for source_file in files
            if not (
                (package_match := _PACKAGE.search(source_file.content)) is not None
                and package_match.group(1) is None
                and package_match.group(2).casefold() in body_packages
            )
        )
        # Resolve qualified package calls against the complete batch symbol set.
        self._functions = tuple(
            function
            for source_file in analysis_files
            for function in self.extract_functions(source_file)[0]
        )
        analysis = super().analyze(analysis_files)
        self._functions = analysis.functions
        return analysis

    def extract_functions(
        self, source_file: SourceFile
    ) -> tuple[list[Function], list[Diagnostic]]:
        lines = source_file.content.splitlines(keepends=True)
        package_match = _PACKAGE.search(source_file.content)
        if package_match is None:
            return [], [
                Diagnostic(
                    code="parse_error",
                    severity="error",
                    message="PL/SQL package declaration was not found.",
                    source_id=source_file.source_id,
                )
            ]

        package = package_match.group(2)
        is_body = package_match.group(1) is not None
        functions: list[Function] = []
        diagnostics: list[Diagnostic] = []
        index = 0
        while index < len(lines):
            match = _MEMBER.match(lines[index])
            if match is None:
                index += 1
                continue

            name = match.group(2) or match.group(3)
            implemented = self._has_implementation(lines, index, match.group("tail"))
            if is_body and not implemented:
                index += 1
                continue
            if not is_body and implemented:
                index += 1
                continue

            if not is_body:
                end = index
                while ";" not in lines[end] and end + 1 < len(lines):
                    end += 1
                source = "".join(lines[index : end + 1])
                functions.append(
                    self._make_function(
                        source_file, package, name, index, end, source, is_body
                    )
                )
                index = end + 1
                continue

            end = self._find_member_end(lines, index, name)
            if end is None:
                diagnostics.append(
                    Diagnostic(
                        code="unterminated_procedure",
                        severity="error",
                        message=f"PL/SQL package member {name!r} has no matching END.",
                        source_id=source_file.source_id,
                        line=index + 1,
                    )
                )
                index += 1
                continue
            functions.append(
                self._make_function(
                    source_file,
                    package,
                    name,
                    index,
                    end,
                    "".join(lines[index : end + 1]),
                    is_body,
                )
            )
            index = end + 1
        return functions, diagnostics

    def extract_raw_calls(self, function: Function) -> list[RawCall]:
        lexer = get_lexer_by_name("sql")
        tokens = [
            (token_type, value, function.source[:position].count("\n"))
            for position, token_type, value in lexer.get_tokens_unprocessed(
                function.source
            )
        ]
        significant = [
            item for item in tokens
            if item[0] not in Text.Whitespace
            and item[0] not in Comment
            and item[0] not in String
        ]
        calls: list[RawCall] = []
        seen: set[tuple[str, int]] = set()
        for index, (token_type, value, offset) in enumerate(significant):
            if token_type not in Name or not re.fullmatch(r"[A-Za-z_]\w*", value):
                continue
            if value.casefold() in _CONTROL_WORDS:
                continue
            previous = significant[index - 1] if index else None
            next_token = significant[index + 1] if index + 1 < len(significant) else None
            previous_value = previous[1] if previous and previous[2] == offset else ""
            next_value = next_token[1] if next_token else ""
            if next_value == ":=" or previous_value in {".", "END", "end"}:
                continue

            qualifier = None
            display_name = value
            if next_value == "." and index + 2 < len(significant):
                qualified = significant[index + 2]
                if qualified[0] in Name and re.fullmatch(r"[A-Za-z_]\w*", qualified[1]):
                    display_name = f"{value}.{qualified[1]}"
                    qualifier = value
                    value = qualified[1]
                    next_value = (
                        significant[index + 3][1]
                        if index + 3 < len(significant)
                        else ""
                    )
            if next_value != "(" and not (
                previous_value.casefold() in {"call", "execute"}
                or next_value == ";"
            ):
                continue
            line = function.source_range.start_line + offset
            if line == function.source_range.start_line:
                continue
            key = (display_name.casefold(), line)
            if key in seen:
                continue
            seen.add(key)
            calls.append(
                RawCall(
                    name=display_name,
                    line=line,
                    context={"package": qualifier},
                )
            )
        return calls

    def resolve_target(
        self,
        raw_call: RawCall,
        source_function: Function,
        symbol_index: dict[str, list[Function]],
    ) -> str | None | tuple[str | None, str | None]:
        package = raw_call.context.get("package")
        name = raw_call.name.rsplit(".", 1)[-1]
        candidates = [
            function for function in self._functions
            if function.name.casefold() == name.casefold()
        ]
        if package:
            candidates = [
                function for function in candidates
                if function.module.casefold() == str(package).casefold()
            ]
        else:
            local = [
                function for function in candidates
                if function.module.casefold() == source_function.module.casefold()
            ]
            if local:
                candidates = local
        if len(candidates) == 1:
            return candidates[0].id
        return None

    @staticmethod
    def _find_member_end(
        lines: list[str], start: int, name: str
    ) -> int | None:
        named_end = re.compile(
            rf"^\s*end\s+{re.escape(name)}\s*;", re.IGNORECASE
        )
        for index in range(start + 1, len(lines)):
            if named_end.match(lines[index]):
                return index
            if _BARE_END.match(lines[index]):
                return index
        return None

    @staticmethod
    def _has_implementation(
        lines: list[str], start: int, first_tail: str
    ) -> bool:
        for index in range(start, len(lines)):
            text = first_tail if index == start else lines[index]
            if _IMPLEMENTATION.search(text):
                return True
            if ";" in text:
                return False
        return False

    @staticmethod
    def _make_function(
        source_file: SourceFile,
        package: str,
        name: str,
        start: int,
        end: int,
        source: str,
        is_body: bool,
    ) -> Function:
        return Function(
            id=f"plsql:{package}:{name}",
            name=name,
            language="plsql",
            module=package,
            source_id=source_file.source_id,
            source=source,
            source_range=SourceRange(start_line=start + 1, end_line=end + 1),
            attributes={
                "visibility": "public",
                "package": package,
                "body": is_body,
                "declaration_only": not is_body,
                "path": PurePath(source_file.source_id).suffix.casefold(),
            },
        )
