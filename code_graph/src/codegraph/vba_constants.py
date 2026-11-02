from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from types import MappingProxyType
from typing import Mapping, Sequence

from .model import Constant, Diagnostic, Function, SourceFile, SourceRange


_TOKEN = re.compile(
    r'"(?:[^"]|"")*"|\'[^\r\n]*|#[^#\r\n]*#|'
    r"&[Hh][0-9A-Fa-f]+&?|&[Oo][0-7]+&?|"
    r"(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][+-]?\d+)?[%&!#@]?|"
    r"[A-Za-z_]\w*(?:[$%&!#@](?![A-Za-z_]))?|[^\s]"
)
_IDENTIFIER = re.compile(r"[A-Za-z_]\w*[$%&!#@]?\Z")
_START = re.compile(
    r"(?i)^(?:(?:public|private|friend|static)\s+)?"
    r"(?:sub|function|property\s+(?:get|let|set))\b"
)
_END = re.compile(r"(?i)^end\s+(?:sub|function|property)\b")
_CONST = re.compile(r"(?i)^(?:(public|private|global)\s+)?const\b\s*(.*)$")
_DECLARATION = re.compile(
    r"(?i)^(?:(?:public|private|global)\s+)?"
    r"(?:dim|static|const)\s+(.+)$"
)


@dataclass(frozen=True)
class _Statement:
    text: str
    line: int
    end_line: int


def _statements(source: str) -> tuple[_Statement, ...]:
    """Split code outside strings/comments, retaining physical line locations."""
    statements: list[_Statement] = []
    pending = ""
    start = 1
    for number, line in enumerate(source.splitlines(), 1):
        end = len(line)
        for match in _TOKEN.finditer(line):
            token = match.group()
            if token.startswith("'"):
                end = match.start()
                break
            if token.casefold() == "rem" and (
                not line[:match.start()].strip()
                or line[:match.start()].rstrip().endswith(":")
            ):
                end = match.start()
                break
        if not pending:
            start = number
        code = line[:end].rstrip()
        if re.search(r"\s_\s*$", code):
            pending += code[:-1] + " "
            continue
        # Continuations can contain quoted colons; tokenize the combined text again.
        text = pending + code
        offset = 0
        for match in _TOKEN.finditer(text):
            if match.group() != ":" or text[match.end():match.end() + 1] == "=":
                continue
            part = text[offset:match.start()].strip()
            if _IDENTIFIER.fullmatch(part):
                part += ":"
            if part:
                statements.append(_Statement(part, start, number))
            offset = match.end()
        part = text[offset:].strip()
        if part:
            statements.append(_Statement(part, start, number))
        pending = ""
    if pending:
        statements.append(_Statement(pending.strip(), start, number))
    return tuple(statements)


def _parts(text: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depth = 0
    for match in _TOKEN.finditer(text):
        token = match.group()
        if token == "," and depth == 0:
            parts.append(text[start:match.start()].strip())
            start = match.end()
            continue
        depth += (token == "(") - (token == ")")
    parts.append(text[start:].strip())
    return parts


def _names(text: str) -> set[str]:
    names: set[str] = set()
    for part in _parts(text):
        match = re.match(
            r"(?i)^(?:(?:optional|byval|byref|paramarray|withEvents)\s+)*"
            r"([A-Za-z_]\w*)",
            part,
        )
        if match:
            names.add(match.group(1).casefold())
    return names


def _parameters(header: str) -> str:
    start: int | None = None
    depth = 0
    for match in _TOKEN.finditer(header):
        token = match.group()
        if token == "(":
            if start is None:
                start = match.end()
            depth += 1
        elif token == ")" and start is not None:
            depth -= 1
            if depth == 0:
                return header[start:match.start()]
    return ""


def _function_uses(function: Function) -> tuple[set[str], tuple[_Statement, ...]]:
    statements = _statements(function.source)
    shadowed = {function.name.casefold()}
    if not statements:
        return shadowed, ()
    parameters = _parameters(statements[0].text)
    shadowed.update(_names(parameters))
    defaults = [
        part.split("=", 1)[1] for part in _parts(parameters) if "=" in part
    ]
    uses: list[_Statement] = []
    if defaults:
        uses.append(_Statement(" , ".join(defaults), 1, statements[0].end_line))
    for statement in statements[1:]:
        if _END.match(statement.text) or statement.text.endswith(":"):
            continue
        declaration = _DECLARATION.match(statement.text)
        if declaration:
            shadowed.update(_names(declaration.group(1)))
            expressions = [
                part.split("=", 1)[1] for part in _parts(declaration.group(1))
                if "=" in part
            ]
            statement = replace(statement, text=" , ".join(expressions))
        uses.append(statement)
    return shadowed, tuple(uses)


@dataclass(frozen=True)
class _ConstantDeclaration:
    source_id: str
    # None retains an unsupported declaration as a resolution blocker.
    constant: Constant | None


@dataclass(frozen=True)
class _ConstantIndex:
    module_constants: Mapping[str, Mapping[str, tuple[_ConstantDeclaration, ...]]]
    public_constants: Mapping[str, tuple[_ConstantDeclaration, ...]]
    modules: Mapping[str, tuple[str, ...]]
    shadowed: Mapping[str, frozenset[str]]
    diagnostics: tuple[Diagnostic, ...]


def _collect_constants(files: Sequence[SourceFile]) -> _ConstantIndex:
    by_file: dict[str, list[Constant]] = defaultdict(list)
    module_constants: dict[str, dict[str, list[_ConstantDeclaration]]] = {
        file.source_id: defaultdict(list) for file in files
    }
    public_constants: dict[str, list[_ConstantDeclaration]] = defaultdict(list)
    modules: dict[str, list[str]] = defaultdict(list)
    globals_by_file: dict[str, set[str]] = defaultdict(set)
    diagnostics: list[Diagnostic] = []

    def record(
        name: str, source_id: str, visibility: str,
        constant: Constant | None = None,
    ) -> None:
        declaration = _ConstantDeclaration(source_id, constant)
        module_constants[source_id][name].append(declaration)
        if visibility == "public":
            public_constants[name].append(declaration)

    def diagnose(
        code: str, message: str, source_id: str, line: int,
        function_id: str | None = None,
    ) -> None:
        diagnostics.append(
            Diagnostic(code, "warning", message, source_id, function_id, line)
        )

    for file in sorted(files, key=lambda item: item.source_id):
        module = PurePosixPath(file.source_id).stem
        modules[module.casefold()].append(file.source_id)
        lines = file.content.splitlines(keepends=True)
        in_procedure = False
        in_enum = False
        conditional_depth = 0
        for statement in _statements(file.content):
            text = statement.text
            if re.match(r"(?i)^#\s*if\b", text):
                conditional_depth += 1
            elif re.match(r"(?i)^#\s*end\s+if\b", text):
                conditional_depth = max(0, conditional_depth - 1)
            if _START.match(text):
                in_procedure = True
                procedure = re.search(
                    r"(?i)(?:sub|function|property\s+(?:get|let|set))"
                    r"\s+([A-Za-z_]\w*)",
                    text,
                )
                if procedure:
                    globals_by_file[file.source_id].add(procedure.group(1).casefold())
            if _END.match(text):
                in_procedure = False
                continue
            if in_procedure:
                continue
            type_declaration = re.match(
                r"(?i)^(?:(?:public|private)\s+)?(enum|type)\s+([A-Za-z_]\w*)",
                text,
            )
            if type_declaration:
                globals_by_file[file.source_id].add(type_declaration.group(2).casefold())
                in_enum = type_declaration.group(1).casefold() == "enum"
                continue
            if re.match(r"(?i)^end\s+enum\b", text):
                in_enum = False
                continue
            if in_enum:
                globals_by_file[file.source_id].update(_names(text))
                continue
            match = _CONST.match(text)
            if not match:
                declaration = re.match(
                    r"(?i)^(?:public|private|global|dim)\s+(?!const\b)(.+)$",
                    text,
                )
                if declaration:
                    globals_by_file[file.source_id].update(_names(declaration.group(1)))
                continue
            visibility = (
                "public" if (match.group(1) or "").casefold()
                in {"public", "global"} else "private"
            )
            if conditional_depth:
                diagnose(
                    "unsupported_constant_declaration",
                    "Conditional VBA Const declarations cannot be resolved reliably.",
                    file.source_id, statement.line,
                )
                for name in _names(match.group(2)):
                    record(name, file.source_id, visibility)
                continue
            for part in _parts(match.group(2)):
                parsed = re.fullmatch(
                    r"([A-Za-z_]\w*[$%&!#@]?)"
                    r"(?:\s+As\s+([A-Za-z_]\w*(?:\s*\.\s*[A-Za-z_]\w*)*))?"
                    r"\s*=\s*(.+)",
                    part, re.IGNORECASE,
                )
                if parsed is None:
                    diagnose(
                        "unsupported_constant_declaration",
                        f"Cannot parse VBA Const declaration: {part}.",
                        file.source_id, statement.line,
                    )
                    for name in _names(part):
                        record(name, file.source_id, visibility)
                    continue
                name, type_name, expression = parsed.groups()
                constant = Constant(
                    name=name.rstrip("$%&!#@"), module=module,
                    source_id=file.source_id,
                    source="".join(lines[statement.line - 1:statement.end_line]),
                    source_range=SourceRange(statement.line, statement.end_line),
                    visibility=visibility,
                    type_name=type_name, expression=expression,
                )
                record(constant.name.casefold(), file.source_id, visibility, constant)
                by_file[file.source_id].append(constant)

    for constants in by_file.values():
        seen: set[str] = set()
        for constant in constants:
            name = constant.name.casefold()
            if name in seen:
                diagnose(
                    "duplicate_constant",
                    f"More than one VBA Const uses {constant.name!r} in this module.",
                    constant.source_id, constant.source_range.start_line,
                )
            seen.add(name)

    return _ConstantIndex(
        module_constants=MappingProxyType({
            source_id: MappingProxyType({
                name: tuple(declarations) for name, declarations in scope.items()
            })
            for source_id, scope in module_constants.items()
        }),
        public_constants=MappingProxyType({
            name: tuple(declarations) for name, declarations in public_constants.items()
        }),
        modules=MappingProxyType({
            name: tuple(source_ids) for name, source_ids in modules.items()
        }),
        shadowed=MappingProxyType({
            file.source_id: frozenset(globals_by_file[file.source_id]) for file in files
        }),
        diagnostics=tuple(diagnostics),
    )


def attach_constants(
    files: Sequence[SourceFile], functions: tuple[Function, ...]
) -> tuple[tuple[Function, ...], tuple[Diagnostic, ...]]:
    index = _collect_constants(files)
    diagnostics = list(index.diagnostics)
    enriched: list[Function] = []
    for function in functions:
        shadowed, uses = _function_uses(function)
        blocked = shadowed | index.shadowed[function.source_id]
        local_constants = index.module_constants[function.source_id]
        selected: dict[tuple[str, int, str], Constant] = {}
        for statement in uses:
            tokens = [match.group() for match in _TOKEN.finditer(statement.text)]
            for position, token in enumerate(tokens):
                if not _IDENTIFIER.fullmatch(token):
                    continue
                name = token.rstrip("$%&!#@").casefold()
                previous = tokens[position - 1] if position else ""
                following = tokens[position + 1] if position + 1 < len(tokens) else ""
                if following in {".", ":="} or following == ":":
                    continue
                if previous.casefold() in {"as", "new", "goto", "gosub", "resume"}:
                    continue
                qualifier: str | None = None
                if previous in {".", "!"}:
                    if previous == "!" or position < 2:
                        continue
                    qualifier = tokens[position - 2].casefold()
                    if qualifier in blocked:
                        continue
                    if position >= 3 and tokens[position - 3] in {".", "!"}:
                        continue
                    module_files = index.modules.get(qualifier, ())
                    if not module_files:
                        continue
                    declarations = tuple(
                        item for item in index.public_constants.get(name, ())
                        if item.source_id in module_files
                        and item.source_id != function.source_id
                    )
                    if function.source_id in module_files:
                        declarations += local_constants.get(name, ())
                else:
                    if name in blocked:
                        continue
                    declarations = local_constants.get(
                        name, index.public_constants.get(name, ())
                    )
                candidates = [
                    item.constant for item in declarations if item.constant is not None
                ]
                line = function.source_range.start_line + statement.line - 1
                if any(item.constant is None for item in declarations):
                    code = "unresolved_constant"
                    message = f"Unsupported declarations prevent resolving VBA constant {token!r}."
                elif not candidates:
                    continue
                elif len(candidates) != 1 or (
                    qualifier is not None and len(index.modules[qualifier]) != 1
                ):
                    code = "ambiguous_constant"
                    message = f"Cannot resolve VBA constant {token!r}."
                else:
                    constant = candidates[0]
                    if len(index.modules[constant.module.casefold()]) != 1:
                        code = "ambiguous_constant"
                        message = f"More than one VBA source uses module name {constant.module!r}."
                    else:
                        selected[(
                            constant.source_id, constant.source_range.start_line,
                            constant.name.casefold(),
                        )] = constant
                        continue
                diagnostics.append(Diagnostic(
                    code=code, severity="warning", message=message,
                    source_id=function.source_id, line=line, function_id=function.id,
                ))
        enriched.append(replace(
            function, constants=tuple(selected[key] for key in sorted(selected))
        ))
    return tuple(enriched), tuple(diagnostics)
