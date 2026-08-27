from __future__ import annotations

from typing import Protocol, Sequence

from .model import FileAnalysis, SourceFile


class LanguageFrontend(Protocol):
    def supports(self, file: SourceFile) -> bool:
        """Return whether this frontend owns the file."""

    def analyze(self, files: Sequence[SourceFile]) -> FileAnalysis:
        """Analyze a batch of files supported by this frontend."""

