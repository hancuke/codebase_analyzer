import pytest

from document_updater import (
    DocumentResult,
    InvalidDocumentResultError,
    InvalidManagedDocumentError,
    ResultKind,
    apply_result,
)


def _upsert(fragment_id: str, markdown: str) -> DocumentResult:
    return DocumentResult(fragment_id, ResultKind.UPSERT, markdown)


def test_upsert_inserts_then_replaces_one_fragment() -> None:
    inserted = apply_result(
        "# Order\n\nHuman introduction.\n",
        _upsert("entry:save", "## Save\n\nOriginal."),
    )
    replaced = apply_result(
        inserted.markdown,
        _upsert("entry:save", "## Save\n\nUpdated."),
    )

    assert inserted.fragment_ids == ("entry:save",)
    assert "Human introduction." in replaced.markdown
    assert "Updated." in replaced.markdown
    assert "Original." not in replaced.markdown
    assert replaced.fragment_ids == ("entry:save",)


def test_new_fragments_append_in_application_order() -> None:
    first = apply_result("", _upsert("entry:first", "First"))
    second = apply_result(first.markdown, _upsert("entry:second", "Second"))

    assert second.fragment_ids == ("entry:first", "entry:second")
    assert second.markdown.index("First") < second.markdown.index("Second")


def test_delete_is_idempotent_and_preserves_human_content() -> None:
    initial = apply_result(
        "Introduction.\n",
        _upsert("entry:save", "Save documentation."),
    ).markdown
    deleted = apply_result(
        initial,
        DocumentResult("entry:save", ResultKind.DELETE),
    )
    repeated = apply_result(
        deleted.markdown,
        DocumentResult("entry:save", ResultKind.DELETE),
    )

    assert deleted.fragment_ids == ()
    assert deleted.markdown == "Introduction.\n"
    assert not repeated.changed


@pytest.mark.parametrize(
    "result",
    [
        DocumentResult("entry:save", ResultKind.DELETE),
    ],
)
def test_result_is_immutable(result: DocumentResult) -> None:
    with pytest.raises(Exception):
        result.fragment_id = "other"  # type: ignore[misc]


def test_rejects_invalid_result_and_managed_document_input() -> None:
    with pytest.raises(InvalidDocumentResultError, match="non-empty"):
        DocumentResult("entry:save", ResultKind.UPSERT, "")
    with pytest.raises(InvalidDocumentResultError, match="reserved"):
        _upsert("entry:save", "<!-- codegraph:fragment:start fragment_id=\"x\" -->")
    with pytest.raises(InvalidManagedDocumentError, match="no end marker"):
        apply_result(
            '<!-- codegraph:fragment:start fragment_id="entry:save" -->\n',
            _upsert("entry:save", "Updated."),
        )
    with pytest.raises(InvalidManagedDocumentError, match="mismatch"):
        apply_result(
            '<!-- codegraph:fragment:start fragment_id="entry:save" -->\n'
            "Original.\n"
            '<!-- codegraph:fragment:end fragment_id="entry:other" -->\n',
            _upsert("entry:save", "Updated."),
        )
    with pytest.raises(InvalidManagedDocumentError, match="must not nest"):
        apply_result(
            '<!-- codegraph:fragment:start fragment_id="entry:save" -->\n'
            '<!-- codegraph:fragment:start fragment_id="entry:other" -->\n',
            _upsert("entry:save", "Updated."),
        )
    with pytest.raises(InvalidManagedDocumentError, match="between managed"):
        apply_result(
            '<!-- codegraph:fragment:start fragment_id="a" -->\nA\n'
            '<!-- codegraph:fragment:end fragment_id="a" -->\n'
            "Ambiguous.\n"
            '<!-- codegraph:fragment:start fragment_id="b" -->\nB\n'
            '<!-- codegraph:fragment:end fragment_id="b" -->\n',
            _upsert("entry:a", "A2"),
        )
