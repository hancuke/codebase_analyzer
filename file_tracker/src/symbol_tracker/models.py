"""Data models for symbol/function-level change tracking."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from filetracker.diff import unified_diff
from filetracker.models import ChangeStatus, FileChange


class SymbolType(Enum):
    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"


@dataclass(frozen=True)
class SymbolExtractionOptions:
    """Controls which Python symbols are included in extraction results."""

    include_classes: bool = False
    include_nested_functions: bool = False


@dataclass(frozen=True)
class SymbolState:
    """Immutable snapshot of a single code symbol (function/method/class)."""

    name: str  # Fully-qualified name, e.g. "UserService.login" or "process_data"
    symbol_type: SymbolType
    declaration: str  # Decorators and complete definition declaration
    content: str  # Complete source of the symbol body
    body_hash: str  # SHA-256 of the symbol body
    start_line: int  # 1-based start line
    end_line: int  # 1-based end line (inclusive)


@dataclass(frozen=True)
class SymbolChange:
    file_path: Path
    symbol_name: str
    status: ChangeStatus
    old_symbol: SymbolState | None
    new_symbol: SymbolState | None

    def __post_init__(self) -> None:
        if isinstance(self.file_path, str):
            object.__setattr__(self, "file_path", Path(self.file_path))

    def diff(self) -> str:
        """Generate a body-level Unified Diff for this symbol change."""
        old = self.old_symbol.content if self.old_symbol else ""
        new = self.new_symbol.content if self.new_symbol else ""
        return unified_diff(
            old, new, from_path=str(self.file_path), to_path=str(self.file_path)
        )


@dataclass(frozen=True)
class FileSymbolChanges:
    """Symbol changes extracted from one immutable :class:`FileChange`."""

    file_change: FileChange
    symbol_changes: tuple[SymbolChange, ...]

    @property
    def has_symbol_changes(self) -> bool:
        return bool(self.symbol_changes)

    def by_status(self, status: ChangeStatus) -> tuple[SymbolChange, ...]:
        """Return symbols matching *status* in this file."""
        return tuple(
            symbol for symbol in self.symbol_changes if symbol.status == status
        )

    @property
    def added(self) -> tuple[SymbolChange, ...]:
        return self.by_status(ChangeStatus.ADDED)

    @property
    def modified(self) -> tuple[SymbolChange, ...]:
        return self.by_status(ChangeStatus.MODIFIED)

    @property
    def deleted(self) -> tuple[SymbolChange, ...]:
        return self.by_status(ChangeStatus.DELETED)
