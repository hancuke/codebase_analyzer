from __future__ import annotations

import html
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from codegraph import AnalysisContext

from .models import DocumentAction, EntryUpdatePlan


_START_MARKER = re.compile(
    r'<!-- codegraph:entry:start entry_id="([^"]+)" -->'
)
_END_MARKER = re.compile(
    r'<!-- codegraph:entry:end entry_id="([^"]+)" -->'
)
_SOURCE_MARKER = re.compile(
    r'<!-- codegraph:source source_id="([^"]+)" -->'
)
_RESERVED_MARKER = "<!-- codegraph:"


class DocumentSyncError(ValueError):
    """Base error for invalid document synchronization input."""


class InvalidDocumentPathError(DocumentSyncError):
    """Raised when a source cannot be mapped safely to a document path."""


class InvalidManagedDocumentError(DocumentSyncError):
    """Raised when managed Markdown markers are malformed or ambiguous."""


class DocumentResultError(DocumentSyncError):
    """Raised when LLM results do not match the entry plans."""


class DocumentStateError(DocumentSyncError):
    """Raised when a plan action conflicts with the current document."""


class DocumentMutationAction(Enum):
    WRITE = "write"
    DELETE = "delete"


@dataclass(frozen=True)
class EntryDocumentResult:
    entry_id: str
    markdown: str

    def __post_init__(self) -> None:
        if not self.entry_id:
            raise DocumentResultError("entry_id must not be empty")
        if _RESERVED_MARKER in self.markdown:
            raise DocumentResultError(
                "LLM result must not contain reserved codegraph markers"
            )


@dataclass(frozen=True)
class SourceDocumentTarget:
    source_id: str
    document_path: str


@dataclass(frozen=True)
class DocumentMutation:
    action: DocumentMutationAction
    target: SourceDocumentTarget
    content: str | None = None

    def __post_init__(self) -> None:
        if self.action is DocumentMutationAction.WRITE and self.content is None:
            raise ValueError("write mutation requires content")
        if self.action is DocumentMutationAction.DELETE and self.content is not None:
            raise ValueError("delete mutation must not contain content")


@dataclass(frozen=True)
class PendingReview:
    entry_id: str
    source_id: str
    document_path: str
    markdown: str | None


@dataclass(frozen=True)
class DocumentSyncPlan:
    mutations: tuple[DocumentMutation, ...]
    pending_reviews: tuple[PendingReview, ...]


@dataclass(frozen=True)
class _ManagedDocument:
    prefix: str
    sections: Mapping[str, str]
    suffix: str


def resolve_document_targets(
    plans: Iterable[EntryUpdatePlan],
    *,
    docs_root: str = "docs",
) -> tuple[SourceDocumentTarget, ...]:
    targets_by_source: dict[str, SourceDocumentTarget] = {}
    sources_by_path: dict[str, str] = {}
    for plan in plans:
        source_id = _plan_source_id(plan)
        target = SourceDocumentTarget(
            source_id=source_id,
            document_path=_document_path(source_id, docs_root),
        )
        previous_source = sources_by_path.get(target.document_path)
        if previous_source is not None and previous_source != source_id:
            raise InvalidDocumentPathError(
                f"sources {previous_source!r} and {source_id!r} map to "
                f"the same document {target.document_path!r}"
            )
        targets_by_source[source_id] = target
        sources_by_path[target.document_path] = source_id
    return tuple(
        sorted(
            targets_by_source.values(),
            key=lambda target: (target.document_path, target.source_id),
        )
    )


def build_document_sync_plan(
    plans: Iterable[EntryUpdatePlan],
    results: Iterable[EntryDocumentResult],
    existing_documents: Mapping[str, str],
    *,
    docs_root: str = "docs",
) -> DocumentSyncPlan:
    ordered_plans = tuple(sorted(plans, key=lambda plan: plan.entry_id))
    plan_ids = {plan.entry_id for plan in ordered_plans}
    if len(plan_ids) != len(ordered_plans):
        raise DocumentResultError("entry plans contain duplicate entry IDs")

    results_by_entry: dict[str, EntryDocumentResult] = {}
    for result in results:
        if result.entry_id in results_by_entry:
            raise DocumentResultError(
                f"duplicate LLM result for entry {result.entry_id!r}"
            )
        results_by_entry[result.entry_id] = result

    unexpected = sorted(set(results_by_entry) - plan_ids)
    if unexpected:
        raise DocumentResultError(
            f"LLM results have no matching plans: {', '.join(unexpected)}"
        )

    required_result_ids = {
        plan.entry_id
        for plan in ordered_plans
        if plan.action in {DocumentAction.CREATE, DocumentAction.UPDATE}
    }
    missing = sorted(required_result_ids - set(results_by_entry))
    if missing:
        raise DocumentResultError(
            f"missing LLM results for entries: {', '.join(missing)}"
        )

    archive_results = sorted(
        plan.entry_id
        for plan in ordered_plans
        if plan.action is DocumentAction.ARCHIVE
        and plan.entry_id in results_by_entry
    )
    if archive_results:
        raise DocumentResultError(
            "archive plans do not accept LLM results: "
            + ", ".join(archive_results)
        )

    targets = {
        target.source_id: target
        for target in resolve_document_targets(
            ordered_plans, docs_root=docs_root
        )
    }
    plans_by_source: dict[str, list[EntryUpdatePlan]] = defaultdict(list)
    pending_reviews: list[PendingReview] = []
    for plan in ordered_plans:
        source_id = _plan_source_id(plan)
        target = targets[source_id]
        if plan.action is DocumentAction.REVIEW:
            result = results_by_entry.get(plan.entry_id)
            pending_reviews.append(
                PendingReview(
                    entry_id=plan.entry_id,
                    source_id=source_id,
                    document_path=target.document_path,
                    markdown=result.markdown if result is not None else None,
                )
            )
            continue
        plans_by_source[source_id].append(plan)

    mutations: list[DocumentMutation] = []
    for source_id in sorted(
        plans_by_source,
        key=lambda item: targets[item].document_path,
    ):
        target = targets[source_id]
        original = existing_documents.get(target.document_path)
        document = _parse_document(
            original if original is not None else _default_preamble(source_id),
            source_id,
        )
        sections = dict(document.sections)

        for plan in sorted(
            plans_by_source[source_id],
            key=lambda item: item.entry_id,
        ):
            exists = plan.entry_id in sections
            if plan.action is DocumentAction.CREATE:
                if exists:
                    raise DocumentStateError(
                        f"create entry {plan.entry_id!r} already exists in "
                        f"{target.document_path!r}"
                    )
                sections[plan.entry_id] = _render_entry_section(
                    plan.entry_id,
                    results_by_entry[plan.entry_id].markdown,
                )
            elif plan.action is DocumentAction.UPDATE:
                if not exists:
                    raise DocumentStateError(
                        f"update entry {plan.entry_id!r} is absent from "
                        f"{target.document_path!r}"
                    )
                sections[plan.entry_id] = _render_entry_section(
                    plan.entry_id,
                    results_by_entry[plan.entry_id].markdown,
                )
            elif plan.action is DocumentAction.ARCHIVE:
                if not exists:
                    raise DocumentStateError(
                        f"archive entry {plan.entry_id!r} is absent from "
                        f"{target.document_path!r}"
                    )
                del sections[plan.entry_id]

        updated_document = _ManagedDocument(
            prefix=document.prefix,
            sections=sections,
            suffix=document.suffix,
        )
        if not sections and _can_delete_document(updated_document, source_id):
            if original is not None:
                mutations.append(
                    DocumentMutation(
                        action=DocumentMutationAction.DELETE,
                        target=target,
                    )
                )
            continue

        content = _render_document(updated_document)
        if content != original:
            mutations.append(
                DocumentMutation(
                    action=DocumentMutationAction.WRITE,
                    target=target,
                    content=content,
                )
            )

    return DocumentSyncPlan(
        mutations=tuple(
            sorted(
                mutations,
                key=lambda mutation: mutation.target.document_path,
            )
        ),
        pending_reviews=tuple(
            sorted(pending_reviews, key=lambda review: review.entry_id)
        ),
    )


def extract_entry_document(
    content: str,
    *,
    source_id: str,
    entry_id: str,
) -> str | None:
    """Return one managed entry's Markdown without structural markers."""

    document = _parse_document(content, source_id)
    section = document.sections.get(entry_id)
    if section is None:
        return None
    lines = section.splitlines()
    return "\n".join(lines[1:-1]).strip()


def _plan_source_id(plan: EntryUpdatePlan) -> str:
    contexts: tuple[AnalysisContext | None, ...]
    if plan.action is DocumentAction.CREATE:
        contexts = (plan.entry.new_context,)
    elif plan.action is DocumentAction.ARCHIVE:
        contexts = (plan.entry.old_context,)
    else:
        contexts = (plan.entry.new_context, plan.entry.old_context)

    functions = tuple(
        function
        for context in contexts
        if context is not None
        for function in context.functions
        if function.id == plan.entry_id
    )
    if not functions:
        raise DocumentSyncError(
            f"entry {plan.entry_id!r} has no function in its required context"
        )
    source_ids = {function.source_id for function in functions}
    if len(source_ids) != 1:
        raise DocumentSyncError(
            f"entry {plan.entry_id!r} resolves to multiple source files"
        )
    return functions[0].source_id


def _document_path(source_id: str, docs_root: str) -> str:
    source_path = _safe_relative_path(source_id, "source_id")
    root_path = _safe_relative_path(docs_root, "docs_root")
    if not source_path.suffix:
        raise InvalidDocumentPathError(
            f"source_id {source_id!r} has no file suffix"
        )
    return str(root_path / source_path.with_suffix(".md"))


def _safe_relative_path(value: str, label: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise InvalidDocumentPathError(
            f"{label} must be a canonical relative POSIX path: {value!r}"
        )
    return path


def _parse_document(content: str, source_id: str) -> _ManagedDocument:
    source_markers = tuple(_SOURCE_MARKER.finditer(content))
    if len(source_markers) > 1:
        raise InvalidManagedDocumentError("document has duplicate source markers")
    if source_markers:
        marked_source_id = html.unescape(source_markers[0].group(1))
        if marked_source_id != source_id:
            raise InvalidManagedDocumentError(
                f"document belongs to source {marked_source_id!r}, "
                f"not {source_id!r}"
            )

    lines = content.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    spans: list[tuple[str, int, int]] = []
    current: tuple[str, int] | None = None
    seen: set[str] = set()
    for index, line in enumerate(lines):
        marker_text = line.rstrip("\r\n")
        start = _START_MARKER.fullmatch(marker_text)
        end = _END_MARKER.fullmatch(marker_text)
        if start is not None:
            entry_id = html.unescape(start.group(1))
            if current is not None:
                raise InvalidManagedDocumentError("entry markers must not nest")
            if entry_id in seen:
                raise InvalidManagedDocumentError(
                    f"duplicate managed entry {entry_id!r}"
                )
            current = (entry_id, offsets[index])
            seen.add(entry_id)
        elif end is not None:
            entry_id = html.unescape(end.group(1))
            if current is None:
                raise InvalidManagedDocumentError(
                    f"entry {entry_id!r} has an end marker without a start"
                )
            if current[0] != entry_id:
                raise InvalidManagedDocumentError(
                    f"entry marker mismatch: {current[0]!r} and {entry_id!r}"
                )
            spans.append((entry_id, current[1], offsets[index] + len(line)))
            current = None
        elif _SOURCE_MARKER.fullmatch(marker_text) is not None:
            continue
        elif _RESERVED_MARKER in marker_text:
            raise InvalidManagedDocumentError(
                f"unrecognized reserved marker on line {index + 1}"
            )
    if current is not None:
        raise InvalidManagedDocumentError(
            f"entry {current[0]!r} has no end marker"
        )
    if not spans:
        return _ManagedDocument(prefix=content, sections={}, suffix="")

    for previous, following in zip(spans, spans[1:]):
        gap = content[previous[2] : following[1]]
        if gap.strip():
            raise InvalidManagedDocumentError(
                "unmanaged content between entry sections is not supported"
            )
    return _ManagedDocument(
        prefix=content[: spans[0][1]],
        sections={
            entry_id: content[start:end].rstrip("\r\n")
            for entry_id, start, end in spans
        },
        suffix=content[spans[-1][2] :],
    )


def _render_entry_section(entry_id: str, markdown: str) -> str:
    escaped_id = html.escape(entry_id, quote=True)
    name = entry_id.rsplit(":", 1)[-1]
    body = markdown.strip()
    parts = [
        f'<!-- codegraph:entry:start entry_id="{escaped_id}" -->',
        f"## {name}",
    ]
    if body:
        parts.append(body)
    parts.append(f'<!-- codegraph:entry:end entry_id="{escaped_id}" -->')
    return "\n\n".join(parts)


def _render_document(document: _ManagedDocument) -> str:
    sections = [
        document.sections[entry_id].strip("\r\n")
        for entry_id in sorted(document.sections)
    ]
    prefix = document.prefix.rstrip("\r\n")
    suffix = document.suffix.strip("\r\n")
    parts = [part for part in (prefix, *sections, suffix) if part]
    return "\n\n".join(parts) + ("\n" if parts else "")


def _default_preamble(source_id: str) -> str:
    escaped_source_id = html.escape(source_id, quote=True)
    name = PurePosixPath(source_id).name
    return (
        f'<!-- codegraph:source source_id="{escaped_source_id}" -->\n'
        f"# {name}\n"
    )


def _can_delete_document(
    document: _ManagedDocument,
    source_id: str,
) -> bool:
    remaining = _render_document(document).strip()
    return not remaining or remaining == _default_preamble(source_id).strip()
