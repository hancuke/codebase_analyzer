import re

from ..domain.reference import Reference, ReferenceKind, SourceLocation
from ..domain.symbol import Symbol


class VbaParser:
    _keyword_names = {
        "if", "then", "else", "elseif", "end", "for", "each", "next",
        "do", "loop", "while", "wend", "select", "case", "with", "new",
        "call", "set", "let", "get", "property", "function", "sub",
        "private", "public", "friend", "dim", "as", "and", "or", "not",
        "true", "false", "nothing", "exit", "on", "error", "resume",
        "debug", "print", "msgbox", "iif",
    }
    _declaration = re.compile(
        r"^\s*(?:(?:public|private|friend|static)\s+)?"
        r"(?:sub|function|property\s+(?:get|let|set))\b",
        re.I,
    )
    _identifier = r"[A-Za-z_][A-Za-z0-9_]*"
    _call = re.compile(rf"\b({_identifier})(?:\s*\.\s*({_identifier}))?\s*(?=\(|$)", re.I)
    _call_statement = re.compile(rf"\bcall\s+({_identifier})(?:\s*\.\s*({_identifier}))?", re.I)

    def parse(self, symbol: Symbol, source: str) -> list[Reference]:
        references: list[Reference] = []
        for line_no, raw_line in enumerate(source.splitlines(), 1):
            line = raw_line.split("'", 1)[0]
            if not line.strip() or self._declaration.match(line):
                continue
            names: list[str] = []
            names.extend(
                f"{a}.{b}" if b else a
                for a, b in self._call_statement.findall(line)
            )
            masked = self._call_statement.sub("", line)
            names.extend(
                f"{a}.{b}" if b else a
                for a, b in self._call.findall(masked)
            )
            for name in names:
                if name.lower() in self._keyword_names:
                    continue
                references.append(Reference(
                    source=symbol.id,
                    target_name=name,
                    kind=ReferenceKind.CALL,
                    location=SourceLocation(symbol.file, line_no),
                ))
        return references
