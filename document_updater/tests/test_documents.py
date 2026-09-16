import pytest

from document_updater import (
    DocumentResult,
    InvalidDocumentResultError,
    InvalidManagedDocumentError,
    ResultKind,
    apply_result,
    get_fragment_markdown,
)


def _upsert(fragment_id: str, markdown: str) -> DocumentResult:
    return DocumentResult(fragment_id, ResultKind.UPSERT, markdown)


def test_upsert_inserts_then_replaces_one_fragment() -> None:
    inserted_markdown = apply_result(
        "# Order\n\nHuman introduction.\n",
        _upsert("entry:save", "## Save\n\nOriginal."),
    )
    replaced_markdown = apply_result(
        inserted_markdown,
        _upsert("entry:save", "## Save\n\nUpdated."),
    )

    assert "Human introduction." in replaced_markdown
    assert "Updated." in replaced_markdown
    assert "Original." not in replaced_markdown


def test_new_fragments_append_in_application_order() -> None:
    first_markdown = apply_result("", _upsert("entry:first", "First"))
    second_markdown = apply_result(
        first_markdown,
        _upsert("entry:second", "Second"),
    )

    assert second_markdown.index("First") < second_markdown.index("Second")


def test_get_fragment_markdown_returns_one_managed_fragment() -> None:
    document = apply_result(
        "# Order\n",
        _upsert("entry:save", "## Save\n\nSaves the order."),
    )

    assert get_fragment_markdown(document, "entry:save") == "## Save\n\nSaves the order."
    assert get_fragment_markdown(document, "entry:missing") is None


def test_delete_is_idempotent_and_preserves_human_content() -> None:
    initial_markdown = apply_result(
        "Introduction.\n",
        _upsert("entry:save", "Save documentation."),
    )
    deleted_markdown = apply_result(
        initial_markdown,
        DocumentResult("entry:save", ResultKind.DELETE),
    )
    repeated_markdown = apply_result(
        deleted_markdown,
        DocumentResult("entry:save", ResultKind.DELETE),
    )

    assert deleted_markdown == "Introduction.\n"
    assert repeated_markdown == deleted_markdown


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
