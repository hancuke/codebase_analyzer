"""Symbol/function-level extraction from caller-provided file changes."""

from __future__ import annotations

from filetracker.models import ChangeStatus, FileChange

from symbol_tracker.base_parser import SymbolParser
from symbol_tracker.models import (
    FileSymbolChanges,
    SymbolChange,
    SymbolExtractionOptions,
    SymbolState,
    SymbolType,
)
from symbol_tracker.registry import ParserRegistry


class SymbolTracker:
    """Extract symbol changes from file changes without scanning the filesystem."""

    def __init__(self, registry: ParserRegistry | None = None):
        self.registry = registry or ParserRegistry()

    def extract_symbol_changes(
        self,
        file_change: FileChange,
        options: SymbolExtractionOptions | None = None,
    ) -> FileSymbolChanges:
        """Extract symbols from one already-scanned file change snapshot."""
        extraction_options = options or SymbolExtractionOptions()
        parser = self.registry.get_parser(str(file_change.path))
        if parser is None:
            return FileSymbolChanges(file_change=file_change, symbol_changes=())

        baseline_symbols = self._extract_symbol_map(
            parser, file_change.baseline_content.text, extraction_options
        )
        working_symbols = self._extract_symbol_map(
            parser, file_change.working_content.text, extraction_options
        )

        if file_change.status == ChangeStatus.ADDED:
            symbols = tuple(
                self._symbol_change(
                    file_change, name, ChangeStatus.ADDED, None, new_symbol
                )
                for name, new_symbol in working_symbols.items()
            )
        elif file_change.status == ChangeStatus.DELETED:
            symbols = tuple(
                self._symbol_change(
                    file_change, name, ChangeStatus.DELETED, old_symbol, None
                )
                for name, old_symbol in baseline_symbols.items()
            )
        else:
            symbols = tuple(
                [
                    *[
                        self._symbol_change(
                            file_change, name, ChangeStatus.ADDED, None, new_symbol
                        )
                        for name, new_symbol in working_symbols.items()
                        if name not in baseline_symbols
                    ],
                    *[
                        self._symbol_change(
                            file_change,
                            name,
                            ChangeStatus.MODIFIED,
                            baseline_symbols[name],
                            new_symbol,
                        )
                        for name, new_symbol in working_symbols.items()
                        if name in baseline_symbols
                        and new_symbol.body_hash != baseline_symbols[name].body_hash
                    ],
                    *[
                        self._symbol_change(
                            file_change,
                            name,
                            ChangeStatus.DELETED,
                            old_symbol,
                            None,
                        )
                        for name, old_symbol in baseline_symbols.items()
                        if name not in working_symbols
                    ],
                ]
            )

        return FileSymbolChanges(file_change=file_change, symbol_changes=symbols)

    @staticmethod
    def _symbol_change(
        file_change: FileChange,
        name: str,
        status: ChangeStatus,
        old_symbol: SymbolState | None,
        new_symbol: SymbolState | None,
    ) -> SymbolChange:
        return SymbolChange(
            file_path=file_change.path,
            symbol_name=name,
            status=status,
            old_symbol=old_symbol,
            new_symbol=new_symbol,
        )

    def _extract_symbol_map(
        self,
        parser: SymbolParser,
        content: str | None,
        options: SymbolExtractionOptions,
    ) -> dict[str, SymbolState]:
        if not content:
            return {}
        return {
            symbol.name: symbol
            for symbol in parser.parse(content)
            if self._is_included(symbol, options)
        }

    @staticmethod
    def _is_included(
        symbol: SymbolState, options: SymbolExtractionOptions
    ) -> bool:
        if symbol.symbol_type is SymbolType.CLASS:
            return options.include_classes
        return options.include_nested_functions or "." not in symbol.name or (
            symbol.symbol_type is SymbolType.METHOD
        )
