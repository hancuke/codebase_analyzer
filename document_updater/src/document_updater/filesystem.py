from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .documents import (
    DocumentMutationAction,
    DocumentSyncPlan,
    InvalidDocumentPathError,
    PendingReview,
    SourceDocumentTarget,
)


class LocalDocumentStore:
    """Caller-side adapter for loading and atomically publishing documents."""

    def __init__(
        self,
        workspace_root: str | Path,
        *,
        review_root: str = ".codegraph-reviews",
    ) -> None:
        self._workspace_root = Path(workspace_root).resolve()
        self._review_root = review_root

    def load(
        self,
        targets: Iterable[SourceDocumentTarget],
    ) -> dict[str, str]:
        documents: dict[str, str] = {}
        for target in targets:
            path = self._resolve_path(target.document_path)
            if path.exists():
                if not path.is_file():
                    raise OSError(f"document path is not a file: {path}")
                documents[target.document_path] = path.read_text(encoding="utf-8")
        return documents

    def apply(self, sync_plan: DocumentSyncPlan) -> None:
        for review in sync_plan.pending_reviews:
            self._write_pending_review(review)
        for mutation in sync_plan.mutations:
            path = self._resolve_path(mutation.target.document_path)
            if mutation.action is DocumentMutationAction.WRITE:
                if mutation.content is None:
                    raise ValueError("write mutation requires content")
                _atomic_write(path, mutation.content)
            else:
                path.unlink()

    def _write_pending_review(self, review: PendingReview) -> None:
        digest = hashlib.sha256(review.entry_id.encode("utf-8")).hexdigest()[:12]
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", review.entry_id).strip("_")
        relative_path = (
            Path(self._review_root)
            / Path(review.source_id).with_suffix("")
            / f"{name}-{digest}.md"
        )
        path = self._resolve_path(str(relative_path.as_posix()))
        body = review.markdown or ""
        content = (
            f'<!-- codegraph:review entry_id="{review.entry_id}" '
            f'source_id="{review.source_id}" -->\n'
            f"# Pending review: {review.entry_id}\n\n"
            f"Target document: `{review.document_path}`\n"
        )
        if body.strip():
            content += f"\n{body.strip()}\n"
        _atomic_write(path, content)

    def _resolve_path(self, relative_path: str) -> Path:
        path = (self._workspace_root / relative_path).resolve()
        try:
            path.relative_to(self._workspace_root)
        except ValueError as error:
            raise InvalidDocumentPathError(
                f"path escapes workspace root: {relative_path!r}"
            ) from error
        return path


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
