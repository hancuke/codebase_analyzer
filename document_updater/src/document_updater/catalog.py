from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping


_FORMAT_VERSION = 1


@dataclass(frozen=True)
class DocumentRegistration:
    """The entry points explicitly covered by one project-relative document."""

    document_path: str
    entry_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        document_path = _canonical_document_path(self.document_path)
        if any(
            not isinstance(entry_id, str) or not entry_id.strip()
            for entry_id in self.entry_ids
        ):
            raise ValueError("Document registration entry IDs must be non-empty strings.")
        entry_ids = tuple(sorted(set(self.entry_ids)))
        if not entry_ids:
            raise ValueError("Document registrations require at least one entry ID.")
        if any(entry_id != entry_id.strip() for entry_id in entry_ids):
            raise ValueError("Document registration entry IDs cannot have surrounding whitespace.")
        if len(entry_ids) != len(self.entry_ids):
            raise ValueError("Document registration entry IDs must be unique.")
        object.__setattr__(self, "document_path", document_path)
        object.__setattr__(self, "entry_ids", entry_ids)


@dataclass(frozen=True)
class DocumentCatalog:
    """A deterministic collection of explicit entry-to-document coverage."""

    registrations: tuple[DocumentRegistration, ...] = ()

    def __post_init__(self) -> None:
        registrations = tuple(
            sorted(self.registrations, key=lambda item: item.document_path)
        )
        paths = [item.document_path for item in registrations]
        if len(paths) != len(set(paths)):
            raise ValueError("Document catalog paths must be unique.")

        entry_ids = [
            entry_id for registration in registrations for entry_id in registration.entry_ids
        ]
        if len(entry_ids) != len(set(entry_ids)):
            raise ValueError("An entry ID can belong to only one document registration.")
        object.__setattr__(self, "registrations", registrations)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> DocumentCatalog:
        documents: dict[str, list[str]] = {}
        for entry_id, document_path in mapping.items():
            documents.setdefault(document_path, []).append(entry_id)
        return cls(
            tuple(
                DocumentRegistration(path, tuple(entry_ids))
                for path, entry_ids in documents.items()
            )
        )

    @classmethod
    def load(cls, path: str | Path) -> DocumentCatalog:
        with Path(path).open(encoding="utf-8") as file:
            payload = json.load(file)
        if not isinstance(payload, dict):
            raise ValueError("Document catalog must be a JSON object.")
        if payload.get("version") != _FORMAT_VERSION:
            raise ValueError(
                f"Document catalog version must be {_FORMAT_VERSION!r}."
            )
        documents = payload.get("documents")
        if not isinstance(documents, list):
            raise ValueError("Document catalog 'documents' must be a list.")
        registrations: list[DocumentRegistration] = []
        for document in documents:
            if not isinstance(document, dict) or set(document) != {"path", "entry_ids"}:
                raise ValueError(
                    "Each document catalog item must contain only 'path' and 'entry_ids'."
                )
            path_value = document["path"]
            entry_ids = document["entry_ids"]
            if not isinstance(path_value, str) or not isinstance(entry_ids, list):
                raise ValueError(
                    "Document catalog paths must be strings and entry_ids must be lists."
                )
            registrations.append(DocumentRegistration(path_value, tuple(entry_ids)))
        return cls(tuple(registrations))

    def save(self, path: str | Path) -> None:
        payload = {
            "version": _FORMAT_VERSION,
            "documents": [
                {
                    "path": registration.document_path,
                    "entry_ids": list(registration.entry_ids),
                }
                for registration in self.registrations
            ],
        }
        with Path(path).open("w", encoding="utf-8") as file:
            json.dump(payload, file, indent=2)
            file.write("\n")

    def document_path_for(self, entry_id: str) -> str | None:
        for registration in self.registrations:
            if entry_id in registration.entry_ids:
                return registration.document_path
        return None

    def entry_ids_for(self, document_path: str) -> tuple[str, ...]:
        canonical_path = _canonical_document_path(document_path)
        for registration in self.registrations:
            if registration.document_path == canonical_path:
                return registration.entry_ids
        return ()


def _canonical_document_path(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Document paths must be non-empty strings.")
    if value != value.strip() or "\\" in value:
        raise ValueError("Document paths must be canonical POSIX paths.")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value != path.as_posix()
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise ValueError("Document paths must be canonical project-relative POSIX paths.")
    return value
