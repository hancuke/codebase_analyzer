"""Local PL/SQL analysis workload; no LLM or dependency traversal."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

class PlsqlAnalysisMode(str, Enum):
    SINGLE_PASS = "single_pass"
    MULTI_PASS = "multi_pass"


class PlsqlWorkloadReason(str, Enum):
    LOGICAL_LENGTH = "logical_length"
    ADJUSTED_CODE_LENGTH = "adjusted_code_length"
    CONTROL_STRUCTURE = "control_structure"
    SQL_STRUCTURE = "sql_structure"
    CURSOR_AND_EXCEPTIONS = "cursor_and_exceptions"
    STRUCTURE_CAP = "structure_cap"
    PHYSICAL_LENGTH = "physical_length"
    SHORT_SOURCE = "short_source"


@dataclass(frozen=True)
class PlsqlWorkloadEvidence:
    reason: PlsqlWorkloadReason
    points: float


@dataclass(frozen=True)
class PlsqlWorkloadMetrics:
    physical_code_lines: int
    adjusted_code_lines: int
    logical_units: float
    decisions: int
    loops: int
    max_control_depth: int
    nested_control_units: int
    sql_statements: int
    joins: int
    additional_query_blocks: int
    set_operations: int
    grouping_clauses: int
    aggregates: int
    windows: int
    complex_mappings: int
    cursor_declarations: int
    fetched_cursors: int
    exception_handlers: int

    @property
    def sql_structure_units(self) -> int:
        return (
            self.joins + self.additional_query_blocks + self.set_operations
            + self.grouping_clauses + self.aggregates + self.windows
        )


@dataclass(frozen=True)
class PlsqlComplexityAssessment:
    mode: PlsqlAnalysisMode
    score: float
    metrics: PlsqlWorkloadMetrics
    evidence: tuple[PlsqlWorkloadEvidence, ...]


_SHORT_SOURCE_UNITS = 50
_MULTI_PASS_SCORE = 100
_EXPRESSION_TOKENS_PER_UNIT = 20
_STRUCTURE_CAP = 30
_PHYSICAL_CAP = 10
_LINES_PER_POINT = 50
_TOKEN = re.compile(
    r"(?P<space>\s+)|(?P<comment>--[^\n]*|/\*.*?\*/)"
    r"|(?P<string>'(?:''|[^'])*')"
    r'|(?P<identifier>"(?:""|[^"])*")'
    r"|(?P<number>\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"
    r"|(?P<word>[a-zA-Z_][a-zA-Z_0-9$#]*)"
    r"|(?P<operator>:=|=>|<=|>=|<>|\|\|)|(?P<other>.)",
    re.DOTALL,
)
_SQL_START = {"select", "insert", "update", "delete", "merge", "with"}
_AGGREGATES = {
    "avg", "count", "sum", "min", "max", "listagg", "stddev", "variance",
    "median", "json_arrayagg", "json_objectagg", "xmlagg",
}


@dataclass(frozen=True)
class _Token:
    value: str
    atom: bool = False
    start_line: int = 1
    end_line: int = 1


def _lex(source: str) -> tuple[list[_Token], int]:
    tokens: list[_Token] = []
    code_lines: set[int] = set()
    position = 0
    line = 1
    while position < len(source):
        start = position
        if source[position : position + 2].casefold() == "q'":
            delimiter = source[position + 2 : position + 3]
            closing = {"[": "]", "{": "}", "(": ")", "<": ">"}.get(
                delimiter, delimiter
            )
            end = source.find(closing + "'", position + 3) if delimiter else -1
            if end < 0:
                raise ValueError(f"Unterminated alternative quoted literal at line {line}.")
            position = end + 2
            value, atom = "<literal>", True
        else:
            match = _TOKEN.match(source, position)
            assert match is not None
            position = match.end()
            text = match.group()
            kind = match.lastgroup
            if (
                source.startswith("/*", start) and kind != "comment"
            ) or (kind == "other" and text in {"'", '"'}):
                raise ValueError(f"Unterminated comment or quoted token at line {line}.")
            if kind in {"space", "comment"}:
                line += text.count("\n")
                continue
            value = "<literal>" if kind in {"string", "number"} else text.casefold()
            atom = kind in {"word", "identifier", "string", "number"}
        raw = source[start:position]
        tokens.append(_Token(value, atom, line, line + raw.count("\n")))
        for offset, part in enumerate(raw.split("\n")):
            if part.strip():
                code_lines.add(line + offset)
        line += raw.count("\n")
    return tokens, len(code_lines)


def _simple_value(tokens: list[_Token]) -> bool:
    if not tokens:
        return False
    if len(tokens) == 2 and tokens[0].value in {"+", "-"}:
        return tokens[1].value == "<literal>"
    if len(tokens) == 2 and tokens[0].value == ":":
        return tokens[1].atom
    if tokens[-1].value == "*" and (
        len(tokens) == 1 or (len(tokens) >= 3 and tokens[-2].value == ".")
    ):
        return len(tokens) == 1 or _simple_value(tokens[:-2])
    return all(
        token.atom if index % 2 == 0 else token.value == "."
        for index, token in enumerate(tokens)
    ) and len(tokens) % 2 == 1


def _expression_units(tokens: list[_Token]) -> int:
    # CASE/query structure has its own units; do not count its tokens again.
    if any(token.value in {"case", "select"} for token in tokens):
        return 0
    return max(
        1, (len(tokens) + _EXPRESSION_TOKENS_PER_UNIT - 1)
        // _EXPRESSION_TOKENS_PER_UNIT,
    )


def _split_expressions(tokens: list[_Token]) -> list[list[_Token]]:
    result: list[list[_Token]] = []
    depth = 0
    case_depth = 0
    start = 0
    for index, token in enumerate(tokens):
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        elif token.value == "case":
            case_depth += 1
        elif token.value == "end" and case_depth:
            case_depth -= 1
        elif token.value == "," and depth == 0 and case_depth == 0:
            result.append(tokens[start:index])
            start = index + 1
    result.append(tokens[start:])
    return result


def _mapping_units(tokens: list[_Token]) -> int:
    """Count nontrivial mapping expressions, not field-list width."""
    units = 0
    index = 0
    while index < len(tokens):
        value = tokens[index].value
        expressions: list[list[_Token]] = []
        if value == "values" and index + 1 < len(tokens) and tokens[index + 1].value == "(":
            end = index + 2
            depth = 1
            while end < len(tokens) and depth:
                depth += (tokens[end].value == "(") - (tokens[end].value == ")")
                end += 1
            expressions = _split_expressions(tokens[index + 2 : end - 1])
            index = end
        elif value == "set":
            end = index + 1
            depth = 0
            case_depth = 0
            while end < len(tokens):
                word = tokens[end].value
                if depth == 0 and case_depth == 0 and word in {
                    "where", "when", "returning", "log",
                }:
                    break
                depth += (word == "(") - (word == ")")
                case_depth += word == "case"
                if word == "end" and case_depth:
                    case_depth -= 1
                end += 1
            for assignment in _split_expressions(tokens[index + 1 : end]):
                equals = next(
                    (i for i, token in enumerate(assignment) if token.value == "="),
                    None,
                )
                if equals is not None:
                    expressions.append(assignment[equals + 1 :])
            index = end
        elif value == "select":
            end = index + 1
            depth = 0
            case_depth = 0
            while end < len(tokens):
                word = tokens[end].value
                if depth == 0 and case_depth == 0 and word in {
                    "from", "into", "union", "intersect", "minus",
                }:
                    break
                depth += (word == "(") - (word == ")")
                case_depth += word == "case"
                if word == "end" and case_depth:
                    case_depth -= 1
                end += 1
            projection = tokens[index + 1 : end]
            if projection and projection[0].value in {"distinct", "unique", "all"}:
                projection = projection[1:]
            for expression in _split_expressions(projection):
                if len(expression) >= 2 and expression[-2].value == "as":
                    expression = expression[:-2]
                elif (
                    len(expression) >= 2 and expression[-1].atom
                    and expression[-2].value not in {
                        ".", "+", "-", "*", "/", "=", "<", ">", "||",
                    } and expression[-1].value != "<literal>"
                ):
                    expression = expression[:-1]
                expressions.append(expression)
            index = end
        else:
            index += 1
        for expression in expressions:
            has_scored_sql_expression = any(
                token.value in _AGGREGATES | {"over"}
                and i + 1 < len(expression) and expression[i + 1].value == "("
                and (i == 0 or expression[i - 1].value != ".")
                for i, token in enumerate(expression)
            )
            if not _simple_value(expression) and not has_scored_sql_expression:
                units += _expression_units(expression)
    return units


def assess_plsql_complexity(source: str) -> PlsqlComplexityAssessment:
    """Recommend one or multiple analysis passes for one complete subprogram.

    Both physical lines and logical units <= 50 are single-pass; otherwise
    score >= 100 is multi-pass. Main length is the greater of logical units
    and code lines adjusted to discount wide plain DML field lists.
    Dynamic SQL is counted as ordinary code; its literal contents are not
    analyzed. No syntax diagnostics or uncertainty overrides are produced.
    Unterminated lexical tokens raise ValueError rather than return partial
    measurements. This heuristic is not a PL/SQL validator.
    """
    tokens, physical_lines = _lex(source)
    counts: dict[str, int] = {
        name: 0 for name in (
            "decisions", "loops", "max_control_depth", "nested_control_units",
            "sql_statements", "joins", "additional_query_blocks", "set_operations",
            "grouping_clauses", "aggregates", "windows", "complex_mappings",
            "cursor_declarations", "exception_handlers",
        )
    }
    logical_quarters = 0
    fetched_cursors: set[str] = set()
    discounted_lines: set[int] = set()
    discounted_tokens: set[_Token] = set()
    stack: list[tuple[str, bool]] = []
    pending_if_headers: set[int] = set()
    buffer: list[_Token] = []

    def measure_sql(statement: list[_Token]) -> int:
        sql_index = next(
            (i for i, token in enumerate(statement) if token.value in _SQL_START),
            None,
        )
        if sql_index is None:
            return 0
        sql = statement[sql_index:]
        counts["sql_statements"] += 1
        units = 1
        for i, token in enumerate(sql):
            word = token.value
            before = sql[i - 1].value if i else ""
            after = sql[i + 1].value if i + 1 < len(sql) else ""
            feature = None
            if word == "select":
                if before == "(":
                    feature = "additional_query_blocks"
            elif word == "join":
                feature = "joins"
            elif word in {"union", "intersect", "minus"}:
                feature = "set_operations"
            elif (word == "group" and after == "by") or word == "having":
                feature = "grouping_clauses"
            elif word in _AGGREGATES and after == "(" and before != ".":
                feature = "aggregates"
            elif word == "over" and after == "(":
                feature = "windows"
            if feature is not None:
                counts[feature] += 1
                units += 1
        mapping = _mapping_units(sql)
        counts["complex_mappings"] += mapping
        if (
            sql[0].value in {"insert", "update", "delete", "merge"}
            and units == 1 and mapping == 0
            and not any(token.value in {"case", "select"} for token in sql)
        ):
            field_end = next(
                (i for i, token in enumerate(sql) if token.value in {"where", "returning", "log"}),
                len(sql),
            )
            for token in sql[1:field_end]:
                discounted_tokens.add(token)
                discounted_lines.update(
                    range(max(sql[0].start_line + 1, token.start_line), token.end_line + 1)
                )
        return units + mapping

    def flush(*, header: bool = False) -> None:
        nonlocal logical_quarters
        if not buffer:
            return
        statement = buffer[:]
        buffer.clear()
        sql_units = measure_sql(statement)
        if sql_units:
            logical_quarters += sql_units * 4
            if statement[0].value == "cursor":
                logical_quarters += 1
            return
        if header:
            logical_quarters += max(0, _expression_units(statement) - 1) * 4
            return
        values = [token.value for token in statement]
        if not any(kind in {"begin", "exception"} for kind, _ in stack):
            logical_quarters += 1
        elif ":=" in values:
            rhs = statement[values.index(":=") + 1 :]
            logical_quarters += (
                1 if _simple_value(rhs) else max(1, _expression_units(rhs)) * 4
            )
        elif values[0] not in {"null", "exception", "declare"}:
            logical_quarters += max(1, _expression_units(statement)) * 4

    index = 0
    # Skip the outer signature, not declarations in its body.
    if tokens and tokens[0].value in {"create", "function", "procedure"}:
        depth = 0
        while index < len(tokens):
            word = tokens[index].value
            depth += (word == "(") - (word == ")")
            index += 1
            if depth == 0 and word in {"is", "as"}:
                break
    while index < len(tokens):
        token = tokens[index]
        word = token.value
        following = tokens[index + 1].value if index + 1 < len(tokens) else ""
        if word == "end":
            expected = following if following in {"if", "loop", "case"} else None
            if stack:
                kind, expression_case = stack[-1]
                if kind == "case" and expression_case:
                    buffer.append(token)
                else:
                    flush()
                pending_if_headers.discard(len(stack))
                stack.pop()
                if kind != "case" or not expression_case:
                    index += 1
                    if expected:
                        index += 1
                    if index < len(tokens) and tokens[index].atom:
                        index += 1
                    if index < len(tokens) and tokens[index].value == ";":
                        index += 1
                    continue
            index += 2 if expected else 1
            continue
        if word == "begin":
            flush()
            stack.append(("begin", False))
        elif word in {"if", "case", "loop"}:
            expression_case = word == "case" and (
                any(
                    item.value in _SQL_START or item.value in {":=", "return"}
                    for item in buffer
                )
                or any(expression for _, expression in stack)
                or len(stack) in pending_if_headers
            )
            if word != "case" or not expression_case:
                flush(header=True)
            stack.append((word, expression_case))
            depth = sum(kind in {"if", "case", "loop"} for kind, _ in stack)
            counts["max_control_depth"] = max(counts["max_control_depth"], depth)
            counts["nested_control_units"] += max(0, depth - 1)
            if word == "loop":
                counts["loops"] += 1
                logical_quarters += 4
            elif word == "if":
                counts["decisions"] += 1
                logical_quarters += 4
                pending_if_headers.add(len(stack))
            else:
                buffer.append(token)
        elif word in {"then", "else", "elsif"} and stack and stack[-1][0] in {
            "if", "case", "exception",
        }:
            expression_case = stack[-1][1]
            merge_clause = (
                any(item.value == "merge" for item in buffer) and not expression_case
            )
            if expression_case or merge_clause:
                buffer.append(token)
            else:
                flush(header=word == "then")
                if word == "then" and stack[-1][0] == "if":
                    pending_if_headers.discard(len(stack))
                if word == "elsif":
                    counts["decisions"] += 1
                    logical_quarters += 4
                    pending_if_headers.add(len(stack))
        elif (
            word == "when" and stack and stack[-1][0] in {"case", "exception"}
            and (
                stack[-1][1]
                or not any(item.value == "merge" for item in buffer)
            )
        ):
            if stack[-1][0] == "case":
                counts["decisions"] += 1
                logical_quarters += 4
            else:
                counts["exception_handlers"] += 1
            if stack[-1][1]:
                buffer.append(token)
            else:
                flush(header=True)
        elif word == "exception" and stack and stack[-1][0] == "begin":
            flush()
            stack[-1] = ("exception", False)
        elif word == ";":
            flush()
        elif word != "declare":
            if word == "cursor" and (not buffer or buffer[-1].value != "ref"):
                counts["cursor_declarations"] += 1
            if word == "fetch":
                fetched_cursors.add(following)
            if word in {"exit", "continue"} and following == "when":
                counts["decisions"] += 1
            buffer.append(token)
        index += 1

    for token in tokens:
        if token not in discounted_tokens:
            discounted_lines.difference_update(range(token.start_line, token.end_line + 1))
    metrics = PlsqlWorkloadMetrics(
        physical_code_lines=physical_lines,
        adjusted_code_lines=max(0, physical_lines - len(discounted_lines)),
        logical_units=logical_quarters / 4,
        fetched_cursors=len(fetched_cursors),
        **counts,
    )
    control_points = (
        2 * metrics.decisions + metrics.loops + 3 * metrics.nested_control_units
    )
    sql_points = 2 * metrics.sql_structure_units
    other_points = (
        metrics.cursor_declarations + metrics.fetched_cursors + metrics.exception_handlers
    )
    raw_structure = control_points + sql_points + other_points
    physical_points = min(_PHYSICAL_CAP, physical_lines / _LINES_PER_POINT)
    evidence = [
        PlsqlWorkloadEvidence(PlsqlWorkloadReason.LOGICAL_LENGTH, metrics.logical_units),
        PlsqlWorkloadEvidence(
            PlsqlWorkloadReason.ADJUSTED_CODE_LENGTH,
            max(0, metrics.adjusted_code_lines - metrics.logical_units),
        ),
        PlsqlWorkloadEvidence(PlsqlWorkloadReason.CONTROL_STRUCTURE, control_points),
        PlsqlWorkloadEvidence(PlsqlWorkloadReason.SQL_STRUCTURE, sql_points),
        PlsqlWorkloadEvidence(PlsqlWorkloadReason.CURSOR_AND_EXCEPTIONS, other_points),
        PlsqlWorkloadEvidence(
            PlsqlWorkloadReason.STRUCTURE_CAP, min(_STRUCTURE_CAP, raw_structure) - raw_structure,
        ),
        PlsqlWorkloadEvidence(PlsqlWorkloadReason.PHYSICAL_LENGTH, physical_points),
    ]
    score = round(sum(item.points for item in evidence), 2)
    if (
        metrics.physical_code_lines <= _SHORT_SOURCE_UNITS
        and metrics.logical_units <= _SHORT_SOURCE_UNITS
    ):
        mode = PlsqlAnalysisMode.SINGLE_PASS
        evidence.append(PlsqlWorkloadEvidence(PlsqlWorkloadReason.SHORT_SOURCE, 0))
    else:
        mode = (
            PlsqlAnalysisMode.MULTI_PASS if score >= _MULTI_PASS_SCORE
            else PlsqlAnalysisMode.SINGLE_PASS
        )
    return PlsqlComplexityAssessment(mode, score, metrics, tuple(evidence))
