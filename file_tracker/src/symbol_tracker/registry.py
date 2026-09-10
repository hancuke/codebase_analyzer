"""Registry mapping file extensions to symbol parsers."""

from __future__ import annotations

from symbol_tracker.base_parser import SymbolParser


class ParserRegistry:
    def __init__(self) -> None:
        self._parsers: dict[str, SymbolParser] = {}

    def register(self, ext: str, parser: SymbolParser) -> None:
        """Register *ext* (e.g. '.py') to *parser*."""
        normalized_ext = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
        self._parsers[normalized_ext] = parser

    def get_parser(self, file_path: str) -> SymbolParser | None:
        ext = "." + file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        return self._parsers.get(ext)
