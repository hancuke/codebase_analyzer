import re

from ..domain.reference import Reference, ReferenceKind, SourceLocation
from ..domain.symbol import Symbol


class JavaParser:
    _call = re.compile(r"\b([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)?)\s*\(")
    _keywords = {"if", "for", "while", "switch", "catch", "new", "super", "this", "return"}

    def parse(self, symbol: Symbol, source: str) -> list[Reference]:
        result: list[Reference] = []
        for line_no, raw_line in enumerate(source.splitlines(), 1):
            line = re.sub(r"//.*$", "", raw_line)
            for match in self._call.finditer(line):
                name = re.sub(r"\s+", "", match.group(1))
                if name.split(".")[-1] in self._keywords:
                    continue
                if line[match.end():].lstrip().startswith("{"):
                    continue
                result.append(Reference(
                    source=symbol.id,
                    target_name=name,
                    kind=ReferenceKind.CALL,
                    location=SourceLocation(symbol.file, line_no, match.start(1) + 1),
                ))
        return result
