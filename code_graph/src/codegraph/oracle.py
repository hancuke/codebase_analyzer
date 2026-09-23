from __future__ import annotations

import re
from dataclasses import dataclass
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


_CONTROL_WORDS = {
    "and", "as", "begin", "case", "close", "commit", "constant", "cursor",
    "declare", "delete", "else", "elsif", "end", "exception", "exit", "for",
    "function", "if", "in", "insert", "into", "is", "loop", "not", "null",
    "open", "or", "out", "package", "procedure", "raise", "return", "rollback",
    "select", "then", "true", "update", "values", "when", "while", "with",
}


@dataclass(frozen=True)
class _PackageMemberDeclaration:
    package: str
    name: str
    source_id: str
    line: int


@dataclass(frozen=True)
class _PlsqlToken:
    value: str
    normalized: str
    line: int
    identifier: str | None = None


@dataclass(frozen=True)
class _Package:
    name: str
    is_body: bool
    declaration_end: int


@dataclass(frozen=True)
class _MemberHeader:
    kind: str
    name: str
    marker: str
    marker_index: int


class OraclePlsqlAnalyzer(BaseAnalyzer):
    """Parser for Oracle PL/SQL package members and their direct calls."""

    language_name = "plsql"
    file_extensions = {".sql", ".pks", ".pkb", ".pls"}
    is_case_sensitive = False

    def __init__(self) -> None:
        self._functions: tuple[Function, ...] = ()
        self._declared_members: dict[tuple[str, str], _PackageMemberDeclaration] = {}

    def analyze(self, files: Sequence[SourceFile]) -> AnalysisResult:
        self._declared_members = self._package_declarations(files)
        analysis_files = tuple(
            source_file
            for source_file in files
            if not (
                (package := self._find_package(self._tokens(source_file.content)))
                is not None
                and not package.is_body
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
        implemented_members = {
            (function.module.casefold(), function.name.casefold())
            for function in analysis.functions
            if function.attributes.get("body") is True
        }
        missing_implementations = tuple(
            Diagnostic(
                code="missing_package_member_implementation",
                severity="warning",
                message=(
                    f"Public PL/SQL package member {package}.{name} has no "
                    "implementation in the analyzed package bodies."
                ),
                source_id=declaration.source_id,
                line=declaration.line,
            )
            for (package, name), declaration in sorted(self._declared_members.items())
            if (package, name) not in implemented_members
        )
        return AnalysisResult(
            functions=analysis.functions,
            calls=analysis.calls,
            entry_points=analysis.entry_points,
            diagnostics=(*analysis.diagnostics, *missing_implementations),
        )

    def detect_entry_point(self, function: Function) -> EntryPoint | None:
        if function.attributes.get("body") is True:
            if function.attributes.get("visibility") != "public":
                return None
            return EntryPoint(
                function_id=function.id,
                kind="package_public_member",
                source="package_spec",
            )
        if function.attributes.get("standalone") is True:
            return EntryPoint(
                function_id=function.id,
                kind=f"standalone_{function.attributes['member_kind']}",
                source="declaration",
            )
        return None

    def extract_functions(
        self, source_file: SourceFile
    ) -> tuple[list[Function], list[Diagnostic]]:
        lines = source_file.content.splitlines(keepends=True)
        tokens = self._tokens(source_file.content)
        package = self._find_package(tokens)
        if package is None:
            return self._extract_standalone_members(source_file, lines, tokens)

        functions: list[Function] = []
        diagnostics: list[Diagnostic] = []
        index = package.declaration_end + 1
        while index < len(tokens):
            token = tokens[index]
            if token.normalized == "begin":
                break
            if token.normalized not in {"procedure", "function"}:
                index += 1
                continue

            header = self._member_header(tokens, index)
            if header is None:
                index += 1
                continue
            implemented = header.marker != ";"
            if package.is_body and not implemented:
                index = header.marker_index + 1
                continue
            if not package.is_body and implemented:
                index += 1
                continue

            end = (
                header.marker_index
                if not implemented
                else self._find_subprogram_end(tokens, header.marker_index)
            )
            if end is None:
                diagnostics.append(
                    Diagnostic(
                        code="unterminated_procedure",
                        severity="error",
                        message=(
                            f"PL/SQL package member {header.name!r} has no matching END."
                        ),
                        source_id=source_file.source_id,
                        line=token.line,
                    )
                )
                index += 1
                continue
            start_line = token.line
            end_line = tokens[end].line
            functions.append(
                self._make_function(
                    source_file,
                    package.name,
                    header.name,
                    start_line,
                    end_line,
                    "".join(lines[start_line - 1 : end_line]),
                    package.is_body,
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

    def _make_function(
        self,
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
            source_range=SourceRange(start_line=start, end_line=end),
            attributes={
                "visibility": (
                    "public"
                    if (package.casefold(), name.casefold()) in self._declared_members
                    else "private"
                ),
                "package": package,
                "body": is_body,
                "declaration_only": not is_body,
                "path": PurePath(source_file.source_id).suffix.casefold(),
            },
        )

    def _extract_standalone_members(
        self,
        source_file: SourceFile,
        lines: list[str],
        tokens: list[_PlsqlToken],
    ) -> tuple[list[Function], list[Diagnostic]]:
        functions: list[Function] = []
        diagnostics: list[Diagnostic] = []
        index = 0
        while index < len(tokens):
            create_index = self._standalone_member_start(tokens, index)
            if create_index is None:
                index += 1
                continue

            member_index = create_index
            while tokens[member_index].normalized not in {"procedure", "function"}:
                member_index += 1
            header = self._member_header(tokens, member_index, qualified=True)
            if header is None or header.marker == ";":
                index = member_index + 1
                continue
            end = self._find_subprogram_end(tokens, header.marker_index)
            if end is None:
                diagnostics.append(
                    Diagnostic(
                        code="unterminated_procedure",
                        severity="error",
                        message=(
                            f"PL/SQL standalone member {header.name!r} "
                            "has no matching END."
                        ),
                        source_id=source_file.source_id,
                        line=tokens[create_index].line,
                    )
                )
                index = member_index + 1
                continue

            name_index = member_index + 1
            first_name = tokens[name_index].identifier
            schema = PurePath(source_file.source_id).stem
            if (
                first_name is not None
                and name_index + 2 < len(tokens)
                and tokens[name_index + 1].value == "."
                and tokens[name_index + 2].identifier is not None
            ):
                schema = first_name
            start_line = tokens[create_index].line
            end_line = tokens[end].line
            functions.append(
                Function(
                    id=f"plsql:standalone:{schema}:{header.name}",
                    name=header.name,
                    language="plsql",
                    module=schema,
                    source_id=source_file.source_id,
                    source="".join(lines[start_line - 1 : end_line]),
                    source_range=SourceRange(
                        start_line=start_line,
                        end_line=end_line,
                    ),
                    attributes={
                        "visibility": "public",
                        "standalone": True,
                        "member_kind": header.kind,
                        "path": PurePath(source_file.source_id).suffix.casefold(),
                    },
                )
            )
            index = end + 1

        if not functions and not diagnostics:
            return [], [
                Diagnostic(
                    code="parse_error",
                    severity="error",
                    message="PL/SQL package or standalone procedure/function declaration was not found.",
                    source_id=source_file.source_id,
                )
            ]
        return functions, diagnostics

    def _package_declarations(
        self, files: Sequence[SourceFile]
    ) -> dict[tuple[str, str], _PackageMemberDeclaration]:
        declarations: dict[tuple[str, str], _PackageMemberDeclaration] = {}
        for source_file in files:
            tokens = self._tokens(source_file.content)
            package = self._find_package(tokens)
            if package is None or package.is_body:
                continue
            index = package.declaration_end + 1
            while index < len(tokens):
                token = tokens[index]
                if token.normalized in {"begin", "end"}:
                    break
                if token.normalized not in {"procedure", "function"}:
                    index += 1
                    continue
                header = self._member_header(tokens, index)
                if header is None:
                    index += 1
                    continue
                key = (package.name.casefold(), header.name.casefold())
                declarations[key] = _PackageMemberDeclaration(
                    package=package.name.casefold(),
                    name=header.name.casefold(),
                    source_id=source_file.source_id,
                    line=token.line,
                )
                index = header.marker_index + 1
        return declarations

    @staticmethod
    def _tokens(content: str) -> list[_PlsqlToken]:
        lexer = get_lexer_by_name("sql")
        tokens: list[_PlsqlToken] = []
        line = 1
        for _, token_type, value in lexer.get_tokens_unprocessed(content):
            token_line = line
            line += value.count("\n")
            if (
                token_type in Text.Whitespace
                or token_type in Comment
                or (token_type in String and token_type not in String.Symbol)
            ):
                continue
            identifier = None
            if token_type in Name or token_type in String.Symbol:
                identifier = value
                if value.startswith('"') and value.endswith('"'):
                    identifier = value[1:-1].replace('""', '"')
            tokens.append(
                _PlsqlToken(
                    value=value,
                    normalized=value.casefold(),
                    line=token_line,
                    identifier=identifier,
                )
            )
        return tokens

    @classmethod
    def _find_package(cls, tokens: list[_PlsqlToken]) -> _Package | None:
        for index, token in enumerate(tokens):
            if token.normalized != "create":
                continue
            cursor = index + 1
            if cls._matches(tokens, cursor, "or", "replace"):
                cursor += 2
            if not cls._matches(tokens, cursor, "package"):
                continue
            cursor += 1
            is_body = cls._matches(tokens, cursor, "body")
            if is_body:
                cursor += 1
            name, cursor = cls._qualified_name(tokens, cursor)
            if name is None:
                continue
            while cursor < len(tokens):
                if tokens[cursor].normalized in {"as", "is"}:
                    return _Package(
                        name=name,
                        is_body=is_body,
                        declaration_end=cursor,
                    )
                if tokens[cursor].value == ";":
                    break
                cursor += 1
        return None

    @classmethod
    def _member_header(
        cls,
        tokens: list[_PlsqlToken],
        index: int,
        *,
        qualified: bool = False,
    ) -> _MemberHeader | None:
        kind = tokens[index].normalized
        if kind not in {"procedure", "function"}:
            return None
        cursor = index + 1
        if qualified:
            name, cursor = cls._qualified_name(tokens, cursor)
        else:
            name = tokens[cursor].identifier if cursor < len(tokens) else None
            cursor += 1
        if name is None:
            return None

        parentheses = 0
        while cursor < len(tokens):
            token = tokens[cursor]
            if token.value == "(":
                parentheses += 1
            elif token.value == ")":
                parentheses = max(0, parentheses - 1)
            elif parentheses == 0 and (
                token.value == ";"
                or token.normalized in {"as", "is", "begin"}
            ):
                return _MemberHeader(
                    kind=kind,
                    name=name,
                    marker=token.normalized if token.value != ";" else ";",
                    marker_index=cursor,
                )
            cursor += 1
        return None

    @classmethod
    def _find_subprogram_end(
        cls, tokens: list[_PlsqlToken], marker_index: int
    ) -> int | None:
        if tokens[marker_index].normalized == "begin":
            return cls._find_block_end(tokens, marker_index)

        index = marker_index + 1
        while index < len(tokens):
            token = tokens[index]
            if token.normalized == "language":
                return cls._next_semicolon(tokens, index)
            if token.normalized in {"procedure", "function"}:
                nested = cls._member_header(tokens, index)
                if nested is not None:
                    if nested.marker == ";":
                        index = nested.marker_index + 1
                        continue
                    nested_end = cls._find_subprogram_end(tokens, nested.marker_index)
                    if nested_end is None:
                        return None
                    index = nested_end + 1
                    continue
            if token.normalized == "begin":
                return cls._find_block_end(tokens, index)
            index += 1
        return None

    @classmethod
    def _find_block_end(
        cls, tokens: list[_PlsqlToken], begin_index: int
    ) -> int | None:
        depth = 1
        index = begin_index + 1
        while index < len(tokens):
            token = tokens[index]
            if token.normalized in {"begin", "case", "if", "loop"}:
                depth += 1
            elif token.normalized == "end":
                depth -= 1
                semicolon = cls._next_semicolon(tokens, index)
                if semicolon is None:
                    return None
                if depth == 0:
                    return semicolon
                index = semicolon
            index += 1
        return None

    @staticmethod
    def _next_semicolon(
        tokens: list[_PlsqlToken], index: int
    ) -> int | None:
        for cursor in range(index + 1, len(tokens)):
            if tokens[cursor].value == ";":
                return cursor
        return None

    @classmethod
    def _standalone_member_start(
        cls, tokens: list[_PlsqlToken], index: int
    ) -> int | None:
        if tokens[index].normalized != "create":
            return None
        cursor = index + 1
        if cls._matches(tokens, cursor, "or", "replace"):
            cursor += 2
        if (
            cursor < len(tokens)
            and tokens[cursor].normalized in {"procedure", "function"}
        ):
            return index
        return None

    @staticmethod
    def _qualified_name(
        tokens: list[_PlsqlToken], index: int
    ) -> tuple[str | None, int]:
        if index >= len(tokens) or tokens[index].identifier is None:
            return None, index
        name = tokens[index].identifier
        index += 1
        if (
            index + 1 < len(tokens)
            and tokens[index].value == "."
            and tokens[index + 1].identifier is not None
        ):
            name = tokens[index + 1].identifier
            index += 2
        return name, index

    @staticmethod
    def _matches(
        tokens: list[_PlsqlToken], index: int, *values: str
    ) -> bool:
        return (
            index + len(values) <= len(tokens)
            and tuple(
                token.normalized
                for token in tokens[index : index + len(values)]
            )
            == values
        )
