"""Abstract base class for symbol parsers."""

from __future__ import annotations

import abc
import hashlib

from symbol_tracker.models import SymbolState


class SymbolParser(abc.ABC):
    @abc.abstractmethod
    def parse(self, code_text: str) -> list[SymbolState]:
        """Parse source text and extract symbols.

        Must return ``[]`` (never raise) on parse failure or empty input.
        """
        raise NotImplementedError

    @staticmethod
    def compute_hash(text: str) -> str:
        return hashlib.sha256(text.strip().encode("utf-8")).hexdigest()
