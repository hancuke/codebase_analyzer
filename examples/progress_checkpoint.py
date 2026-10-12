"""JSON-backed success checkpoints for caller-defined processing units."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import tempfile


def atomic_write_text(path: Path, text: str) -> None:
    """Replace a text file only after its complete contents have been flushed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False,
            prefix=f".{path.name}.", suffix=".tmp",
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(text)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


@dataclass(frozen=True)
class _SuccessRecord:
    input_digest: str
    output_digest: str


class ProgressCheckpoint:
    """Own durable success records for one single-writer processing workflow.

    The caller owns unit IDs, digest calculation, and processing/publication.
    Record success only after publication completes; clear only after the
    workflow's final commit. Errors propagate without changing in-memory state.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._completed = self._load()

    def matches(
        self, unit_id: str, input_digest: str, output_digest: str,
    ) -> bool:
        """Whether the unit previously succeeded with these inputs and outputs."""
        return self._completed.get(unit_id) == _SuccessRecord(
            input_digest, output_digest,
        )

    def record_success(
        self, unit_id: str, input_digest: str, output_digest: str,
    ) -> None:
        """Persist success immediately; replace any stale record for this unit."""
        completed = dict(self._completed)
        completed[unit_id] = _SuccessRecord(input_digest, output_digest)
        self._save(completed)
        self._completed = completed

    def clear(self) -> None:
        """Persist an empty checkpoint after the caller's final commit."""
        self._save({})
        self._completed = {}

    def _load(self) -> dict[str, _SuccessRecord]:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"invalid progress checkpoint: {self._path}")
        completed: dict[str, _SuccessRecord] = {}
        for unit_id, record in data.items():
            if (
                not isinstance(unit_id, str)
                or not isinstance(record, list)
                or len(record) != 2
                or not isinstance(record[0], str)
                or not isinstance(record[1], str)
            ):
                raise ValueError(f"invalid progress checkpoint for {unit_id!r}")
            completed[unit_id] = _SuccessRecord(record[0], record[1])
        return completed

    def _save(self, completed: dict[str, _SuccessRecord]) -> None:
        data = {
            unit_id: [record.input_digest, record.output_digest]
            for unit_id, record in completed.items()
        }
        atomic_write_text(
            self._path, json.dumps(data, sort_keys=True, indent=2) + "\n",
        )
