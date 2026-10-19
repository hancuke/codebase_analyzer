from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import Sequence

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


def _module_kind(file: SourceFile) -> str:
    path = PurePosixPath(file.source_id)
    suffix = path.suffix.casefold()
    if suffix == ".bas" or (
        suffix == ".txt" and path.stem.casefold().startswith("module_")
    ):
        return "standard"
    if suffix in {".cls", ".frm"} or (
        suffix == ".txt" and path.stem.casefold().startswith("form_")
    ) or re.search(
        r"(?im)^\s*(?:VERSION\s+[\d.]+\s+CLASS|Attribute\s+VB_PredeclaredId)",
        file.content,
    ):
        return "instance"
    return "unknown"


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
class _ConstantIndex:
    by_name: dict[str, list[Constant]]
    modules: dict[str, list[SourceFile]]
    shadowed: dict[str, set[str]]
    uncertain: dict[str, list[tuple[str, str]]]
    kinds: dict[str, str]
    diagnostics: tuple[Diagnostic, ...]


def _collect_constants(files: Sequence[SourceFile]) -> _ConstantIndex:
    by_name: dict[str, list[Constant]] = defaultdict(list)
    by_file: dict[str, list[Constant]] = defaultdict(list)
    modules: dict[str, list[SourceFile]] = defaultdict(list)
    globals_by_file: dict[str, set[str]] = defaultdict(set)
    uncertain: dict[str, list[tuple[str, str]]] = defaultdict(list)
    kinds = {file.source_id: _module_kind(file) for file in files}
    diagnostics: list[Diagnostic] = []

    def diagnose(
        code: str, message: str, source_id: str, line: int,
        function_id: str | None = None,
    ) -> None:
        diagnostics.append(
            Diagnostic(code, "warning", message, source_id, function_id, line)
        )

    for file in sorted(files, key=lambda item: item.source_id):
        module = PurePosixPath(file.source_id).stem
        modules[module.casefold()].append(file)
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
            if conditional_depth:
                diagnose(
                    "unsupported_constant_declaration",
                    "Conditional VBA Const declarations cannot be resolved reliably.",
                    file.source_id, statement.line,
                )
                for name in _names(match.group(2)):
                    uncertain[name].append((
                        file.source_id, (match.group(1) or "private").casefold()
                    ))
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
                        uncertain[name].append((
                            file.source_id, (match.group(1) or "private").casefold()
                        ))
                    continue
                name, type_name, expression = parsed.groups()
                constant = Constant(
                    name=name.rstrip("$%&!#@"), module=module,
                    source_id=file.source_id,
                    source="".join(lines[statement.line - 1:statement.end_line]),
                    source_range=SourceRange(statement.line, statement.end_line),
                    visibility="public" if (match.group(1) or "").casefold()
                    in {"public", "global"} else "private",
                    type_name=type_name, expression=expression,
                )
                by_name[constant.name.casefold()].append(constant)
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
        by_name, modules, globals_by_file, uncertain, kinds, tuple(diagnostics)
    )


def attach_constants(
    files: Sequence[SourceFile], functions: tuple[Function, ...]
) -> tuple[tuple[Function, ...], tuple[Diagnostic, ...]]:
    index = _collect_constants(files)
    diagnostics = list(index.diagnostics)
    public_files = {
        source_id for source_id, kind in index.kinds.items() if kind != "instance"
    }
    enriched: list[Function] = []
    for function in functions:
        shadowed, uses = _function_uses(function)
        blocked = shadowed | index.shadowed[function.source_id]
        visible_files = public_files | {function.source_id}
        selected: dict[tuple[str, int, str], Constant] = {}
        for statement in uses:
            tokens = [match.group() for match in _TOKEN.finditer(statement.text)]
            for position, token in enumerate(tokens):
                if not _IDENTIFIER.fullmatch(token):
                    continue
                name = token.rstrip("$%&!#@").casefold()
                candidates = index.by_name.get(name, [])
                uncertain = index.uncertain.get(name, [])
                if not candidates and not uncertain:
                    continue
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
                    module_files = index.modules.get(qualifier, [])
                    if not module_files:
                        continue
                    scope_files = {file.source_id for file in module_files}
                else:
                    if name in blocked:
                        continue
                    has_local = any(
                        item.source_id == function.source_id for item in candidates
                    ) or any(
                        source_id == function.source_id
                        for source_id, _ in uncertain
                    )
                    scope_files = {function.source_id} if has_local else visible_files
                allowed = scope_files & visible_files
                candidates = [
                    item for item in candidates
                    if item.source_id in allowed
                    and (item.source_id == function.source_id or item.visibility == "public")
                ]
                uncertain_sources = [
                    source_id for source_id, visibility in uncertain
                    if source_id in allowed
                    and (source_id == function.source_id or visibility in {"public", "global"})
                ]
                line = function.source_range.start_line + statement.line - 1
                if uncertain_sources:
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
                    elif (
                        constant.source_id != function.source_id
                        and index.kinds[constant.source_id] == "unknown"
                    ):
                        code = "unknown_constant_module"
                        message = f"Cannot establish standard-module visibility for {token!r}."
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
