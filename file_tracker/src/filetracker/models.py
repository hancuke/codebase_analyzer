"""Data models for physical file-level change tracking."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from filetracker.diff import unified_diff


class ChangeStatus(Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"


class ContentAvailability(Enum):
    """Reason a file version is or is not available as UTF-8 text."""

    ABSENT = "absent"
    TEXT = "text"
    BINARY = "binary"
    UNDECODABLE = "undecodable"
    UNREADABLE = "unreadable"


@dataclass(frozen=True)
class ContentSnapshot:
    """Captured content for one side of a file change."""

    availability: ContentAvailability
    text: str | None = None

    def __post_init__(self) -> None:
        if self.availability is ContentAvailability.TEXT and self.text is None:
            raise ValueError("Text content snapshots require text.")
        if self.availability is not ContentAvailability.TEXT and self.text is not None:
            raise ValueError("Unavailable content snapshots cannot contain text.")

    @property
    def is_text(self) -> bool:
        return self.availability is ContentAvailability.TEXT


@dataclass(frozen=True)
class FileState:
    """Immutable snapshot of a file's metadata."""

    path: Path
    exists: bool
    size: int | None
    mtime: float | None
    sha256: str | None

    def __post_init__(self) -> None:
        if isinstance(self.path, str):
            object.__setattr__(self, "path", Path(self.path))


@dataclass(frozen=True)
class FileChange:
    """Immutable file-level change captured by one :meth:`FileTracker.scan`."""

    path: Path
    status: ChangeStatus
    baseline_state: FileState | None
    working_state: FileState | None
    baseline_content: ContentSnapshot
    working_content: ContentSnapshot

    def __post_init__(self) -> None:
        if isinstance(self.path, str):
            object.__setattr__(self, "path", Path(self.path))

    def diff(self) -> str:
        """Generate a file-level diff from captured UTF-8 text content."""
        if not self.has_text_diff:
            return ""
        return unified_diff(
            self.baseline_content.text or "",
            self.working_content.text or "",
            str(self.path),
            str(self.path),
        )

    @property
    def has_text_diff(self) -> bool:
        """Whether both sides are textual or intentionally absent."""
        text_or_absent = {
            ContentAvailability.TEXT,
            ContentAvailability.ABSENT,
        }
        return (
            self.baseline_content.availability in text_or_absent
            and self.working_content.availability in text_or_absent
        )


@dataclass(frozen=True)
class ChangeSet:
    """Ordered collection of file change snapshots."""

    files: tuple[FileChange, ...]
    baseline_revision: str
    working_revision: str

    @property
    def has_changes(self) -> bool:
        return bool(self.files)

    @property
    def total(self) -> int:
        return len(self.files)

    def by_status(self, status: ChangeStatus) -> tuple[FileChange, ...]:
        """Return files matching *status* while preserving scan order."""
        return tuple(change for change in self.files if change.status == status)

    @property
    def added(self) -> tuple[FileChange, ...]:
        """Compatibility view of added files."""
        return self.by_status(ChangeStatus.ADDED)

    @property
    def modified(self) -> tuple[FileChange, ...]:
        """Compatibility view of modified files."""
        return self.by_status(ChangeStatus.MODIFIED)

    @property
    def deleted(self) -> tuple[FileChange, ...]:
        """Compatibility view of deleted files."""
        return self.by_status(ChangeStatus.DELETED)

    def __iter__(self):
        return iter(self.files)
