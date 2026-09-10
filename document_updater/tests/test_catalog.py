from __future__ import annotations

import json

import pytest

from document_updater import DocumentCatalog, DocumentRegistration


def test_catalog_round_trip_is_deterministic(tmp_path) -> None:
    catalog = DocumentCatalog(
        (
            DocumentRegistration(
                "docs/forms/order.md",
                (
                    "vba:frmOrder:bSave_Click",
                    "vba:frmOrder:bCancel_Click",
                ),
            ),
        )
    )
    path = tmp_path / "document-registry.json"

    catalog.save(path)

    assert DocumentCatalog.load(path) == catalog
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "documents": [
            {
                "path": "docs/forms/order.md",
                "entry_ids": [
                    "vba:frmOrder:bCancel_Click",
                    "vba:frmOrder:bSave_Click",
                ],
            }
        ],
    }


@pytest.mark.parametrize(
    "document_path",
    ("", "/docs/forms/order.md", "docs/../forms/order.md", "docs\\forms\\order.md"),
)
def test_registration_rejects_noncanonical_document_paths(document_path: str) -> None:
    with pytest.raises(ValueError, match="Document paths"):
        DocumentRegistration(document_path, ("vba:frmOrder:bSave_Click",))


def test_catalog_rejects_multiple_documents_for_one_entry() -> None:
    with pytest.raises(ValueError, match="only one document"):
        DocumentCatalog(
            (
                DocumentRegistration(
                    "docs/forms/order.md", ("vba:frmOrder:bSave_Click",)
                ),
                DocumentRegistration(
                    "docs/forms/order-details.md", ("vba:frmOrder:bSave_Click",)
                ),
            )
        )


def test_catalog_from_mapping_groups_entries_by_document_path() -> None:
    catalog = DocumentCatalog.from_mapping(
        {
            "vba:frmOrder:bSave_Click": "docs/forms/order.md",
            "vba:frmOrder:bCancel_Click": "docs/forms/order.md",
        }
    )

    assert catalog.registrations == (
        DocumentRegistration(
            "docs/forms/order.md",
            (
                "vba:frmOrder:bCancel_Click",
                "vba:frmOrder:bSave_Click",
            ),
        ),
    )


def test_catalog_rejects_empty_coverage() -> None:
    with pytest.raises(ValueError, match="at least one entry"):
        DocumentRegistration("docs/forms/order.md", ())
