from __future__ import annotations

import html
import re
from dataclasses import dataclass
from enum import Enum


_FRAGMENT_START = re.compile(
    r'<!-- codegraph:fragment:start fragment_id="([^"]+)" -->'
)
_FRAGMENT_END = re.compile(
    r'<!-- codegraph:fragment:end fragment_id="([^"]+)" -->'
)
_RESERVED_MARKER = "<!-- codegraph:"


class InvalidDocumentResultError(ValueError):
    """Raised when a requested fragment state is invalid."""


class InvalidManagedDocumentError(ValueError):
    """Raised when managed Markdown markers are malformed or ambiguous."""


class ResultKind(Enum):
    UPSERT = "upsert"
    DELETE = "delete"


@dataclass(frozen=True)
class DocumentResult:
    """The desired state of one stable Markdown fragment."""

    fragment_id: str
    kind: ResultKind
    markdown: str | None = None

    def __post_init__(self) -> None:
        if (
            not self.fragment_id
            or self.fragment_id != self.fragment_id.strip()
            or '"' in self.fragment_id
            or "\n" in self.fragment_id
            or "\r" in self.fragment_id
        ):
            raise InvalidDocumentResultError("fragment_id must be a non-empty marker-safe ID")
        if self.kind is ResultKind.UPSERT:
            if self.markdown is None or not self.markdown.strip():
                raise InvalidDocumentResultError(
                    "upsert result requires non-empty Markdown"
                )
            if _RESERVED_MARKER in self.markdown:
                raise InvalidDocumentResultError(
                    "fragment Markdown must not contain reserved codegraph markers"
                )
        elif self.markdown is not None:
            raise InvalidDocumentResultError(
                "delete result must not contain Markdown"
            )


@dataclass(frozen=True)
class AppliedDocument:
    """The rendered Markdown after applying one fragment desired state."""

    markdown: str
    fragment_ids: tuple[str, ...]
    changed: bool


@dataclass(frozen=True)
class _ManagedDocument:
    prefix: str
    fragments: tuple[DocumentResult, ...]
    suffix: str


def apply_result(
    existing_markdown: str,
    result: DocumentResult,
) -> AppliedDocument:
    """Apply one fragment result to caller-selected Markdown."""
    document = _parse_document(existing_markdown)
    fragments = list(document.fragments)
    existing_index = next(
        (
            index
            for index, fragment in enumerate(fragments)
            if fragment.fragment_id == result.fragment_id
        ),
        None,
    )
    if result.kind is ResultKind.UPSERT:
        if existing_index is None:
            fragments.append(result)
        else:
            fragments[existing_index] = result
    elif existing_index is not None:
        del fragments[existing_index]

    updated = _ManagedDocument(
        prefix=document.prefix,
        fragments=tuple(fragments),
        suffix=document.suffix,
    )
    markdown = _render_document(updated)
    return AppliedDocument(
        markdown=markdown,
        fragment_ids=tuple(fragment.fragment_id for fragment in fragments),
        changed=markdown != existing_markdown,
    )


def _parse_document(content: str) -> _ManagedDocument:
    lines = content.splitlines(keepends=True)
    offsets: list[int] = []
    offset = 0
    for line in lines:
        offsets.append(offset)
        offset += len(line)

    fragments: list[DocumentResult] = []
    spans: list[tuple[int, int]] = []
    open_fragment: tuple[str, int, int] | None = None
    seen_ids: set[str] = set()
    for index, line in enumerate(lines):
        marker = line.rstrip("\r\n")
        start = _FRAGMENT_START.fullmatch(marker)
        end = _FRAGMENT_END.fullmatch(marker)
        if start is not None:
            if open_fragment is not None:
                raise InvalidManagedDocumentError(
                    "managed fragment markers must not nest"
                )
            fragment_id = html.unescape(start.group(1))
            _validate_parsed_fragment_id(fragment_id)
            if fragment_id in seen_ids:
                raise InvalidManagedDocumentError(
                    f"duplicate managed fragment {fragment_id!r}"
                )
            seen_ids.add(fragment_id)
            open_fragment = (fragment_id, offsets[index], offsets[index] + len(line))
            continue
        if end is not None:
            if open_fragment is None:
                raise InvalidManagedDocumentError(
                    "managed fragment has an end marker without a start"
                )
            fragment_id = html.unescape(end.group(1))
            if fragment_id != open_fragment[0]:
                raise InvalidManagedDocumentError(
                    f"managed fragment marker mismatch: {open_fragment[0]!r} "
                    f"and {fragment_id!r}"
                )
            body = content[open_fragment[2] : offsets[index]].strip()
            fragments.append(
                DocumentResult(
                    fragment_id=fragment_id,
                    kind=ResultKind.UPSERT,
                    markdown=body,
                )
            )
            spans.append((open_fragment[1], offsets[index] + len(line)))
            open_fragment = None
            continue
        if _RESERVED_MARKER in marker:
            raise InvalidManagedDocumentError(
                f"unrecognized reserved marker on line {index + 1}"
            )
    if open_fragment is not None:
        raise InvalidManagedDocumentError(
            f"fragment {open_fragment[0]!r} has no end marker"
        )
    if not spans:
        return _ManagedDocument(prefix=content, fragments=(), suffix="")

    for previous, following in zip(spans, spans[1:]):
        if content[previous[1] : following[0]].strip():
            raise InvalidManagedDocumentError(
                "unmanaged content between managed fragments is not supported"
            )
    return _ManagedDocument(
        prefix=content[: spans[0][0]],
        fragments=tuple(fragments),
        suffix=content[spans[-1][1] :],
    )


def _validate_parsed_fragment_id(fragment_id: str) -> None:
    try:
        DocumentResult(fragment_id, ResultKind.DELETE)
    except InvalidDocumentResultError as error:
        raise InvalidManagedDocumentError(
            f"invalid managed fragment ID: {fragment_id!r}"
        ) from error


def _render_document(document: _ManagedDocument) -> str:
    parts = [
        part
        for part in (
            document.prefix.strip("\r\n"),
            *(_render_fragment(fragment) for fragment in document.fragments),
            document.suffix.strip("\r\n"),
        )
        if part
    ]
    return "\n\n".join(parts) + ("\n" if parts else "")


def _render_fragment(fragment: DocumentResult) -> str:
    escaped_id = html.escape(fragment.fragment_id, quote=True)
    return "\n".join(
        (
            f'<!-- codegraph:fragment:start fragment_id="{escaped_id}" -->',
            fragment.markdown.strip(),
            f'<!-- codegraph:fragment:end fragment_id="{escaped_id}" -->',
        )
    )
