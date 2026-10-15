import pytest

import document_updater.documents as documents
from document_updater import (
    DocumentResult,
    InvalidManagedDocumentError,
    ResultKind,
    apply_result,
    apply_results,
    get_fragment_markdown,
)


def test_batch_preserves_order_and_applies_repeated_ids_sequentially() -> None:
    original = apply_results(
        "# Introduction\n",
        (
            DocumentResult("a", ResultKind.UPSERT, "Old A"),
            DocumentResult("b", ResultKind.UPSERT, "Old B"),
            DocumentResult("untouched", ResultKind.UPSERT, "Keep me"),
        ),
    )
    updated = apply_results(
        original,
        (
            DocumentResult("b", ResultKind.UPSERT, "Updated B"),
            DocumentResult("a", ResultKind.DELETE),
            DocumentResult("missing", ResultKind.DELETE),
            DocumentResult("c", ResultKind.UPSERT, "First C"),
            DocumentResult("a", ResultKind.UPSERT, "New A"),
            DocumentResult("c", ResultKind.UPSERT, "Final C"),
        ),
    )
    assert updated.startswith("# Introduction\n")
    positions = [
        updated.index(f'fragment_id="{key}"') for key in ("b", "untouched", "c", "a")
    ]
    assert positions == sorted(positions)
    assert get_fragment_markdown(updated, "b") == "Updated B"
    assert get_fragment_markdown(updated, "untouched") == "Keep me"
    assert get_fragment_markdown(updated, "c") == "Final C"
    assert get_fragment_markdown(updated, "a") == "New A"


def test_batch_keeps_suffix_after_delete_all_and_insert() -> None:
    original = apply_result("# Introduction\n", DocumentResult("a", ResultKind.UPSERT, "A"))
    original += "\nFooter\n"
    updated = apply_results(
        original,
        (
            DocumentResult("a", ResultKind.DELETE),
            DocumentResult("b", ResultKind.UPSERT, "B"),
        ),
    )
    assert updated.startswith("# Introduction\n")
    assert updated.endswith("\nFooter\n")
    assert get_fragment_markdown(updated, "a") is None
    assert get_fragment_markdown(updated, "b") == "B"


def test_batch_parses_and_renders_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    parse = documents._parse_document
    render = documents._render_document

    def counted_parse(markdown):
        calls.append("parse")
        return parse(markdown)

    def counted_render(document):
        calls.append("render")
        return render(document)

    monkeypatch.setattr(documents, "_parse_document", counted_parse)
    monkeypatch.setattr(documents, "_render_document", counted_render)
    apply_results(
        "",
        tuple(DocumentResult(str(index), ResultKind.UPSERT, "Body") for index in range(10)),
    )
    assert calls == ["parse", "render"]


def test_empty_batch_preserves_original_text_but_validates_markers() -> None:
    original = "\r\n# Introduction\r\n\r\n"
    assert apply_results(original, ()) == original
    with pytest.raises(InvalidManagedDocumentError):
        apply_results('<!-- codegraph:fragment:end fragment_id="a" -->\n', ())
    with pytest.raises(InvalidManagedDocumentError):
        apply_results(
            '<!-- codegraph:fragment:end fragment_id="a" -->\n',
            (DocumentResult("a", ResultKind.DELETE),),
        )


def test_single_result_matches_batch_and_delete_can_empty_document() -> None:
    result = DocumentResult("a", ResultKind.UPSERT, "Body")
    markdown = apply_result("", result)
    assert markdown == apply_results("", (result,))
    assert apply_results(markdown, (DocumentResult("a", ResultKind.DELETE),)) == ""
