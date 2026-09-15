"""Run a complete caller-owned documentation lifecycle.

This example deliberately keeps document paths, storage, grouping, and baseline
advancement outside the three reusable packages. Run it with:

    uv run python examples/documentation_lifecycle.py
"""

from __future__ import annotations

import tempfile
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

from change_analyzer import EntryChange, analyze_changes, entry_changes
from codegraph import SourceFile, VbaAnalyzer
from document_updater import (
    AuthorRequest,
    DocumentResult,
    FilePromptRenderer,
    LlmDocumentAuthor,
    PromptBlock,
    PromptMetadata,
    ResultKind,
)
from document_updater import apply_result
from filetracker import FileTracker


DocumentKey = str


class DemoLlmClient:
    """A deterministic stand-in for a real LLM provider adapter."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        entry_id = user_prompt.split("<Context_entry_id>\n", 1)[1].split(
            "\n</Context_entry_id>", 1
        )[0]
        return (
            f"## {entry_id.rsplit(':', 1)[-1]}\n\n"
            f"This generated fragment documents `{entry_id}`."
        )


def load_vba_sources(source_root: Path) -> tuple[SourceFile, ...]:
    """Load the complete current source snapshot required by change_analyzer."""
    return tuple(
        SourceFile(
            path.relative_to(source_root).as_posix(),
            path.read_text(encoding="utf-8"),
        )
        for path in sorted(source_root.rglob("*"))
        if path.is_file() and path.suffix.casefold() in {".bas", ".frm"}
    )


def request_for_entry(change: EntryChange) -> AuthorRequest:
    """The application selects the Entry facts it exposes to the author."""
    context = "\n\n".join(
        function.source
        for analysis_context in (change.old_context, change.new_context)
        if analysis_context is not None
        for function in analysis_context.functions
    )
    return AuthorRequest(
        fragment_id=fragment_id_for(change),
        blocks=(
            PromptBlock("entry_id", change.entry_id),
            PromptBlock("code_context", context),
            PromptBlock(
                "function_changes",
                "\n".join(item.diff for item in change.function_changes),
            ),
        ),
        metadata=PromptMetadata(
            project="Order System",
            language="VBA",
            glossary=(
                "- Entry: a documentation entry point.\n"
                "- Fragment: a managed Markdown section."
            ),
        ),
    )


def fragment_id_for(change: EntryChange) -> str:
    return f"entry:{change.entry_id}"


def document_key_for(change: EntryChange) -> DocumentKey:
    """Caller policy: Entries from one source file share one Markdown document."""
    for context in (change.new_context, change.old_context):
        if context is None:
            continue
        for function in context.functions:
            if function.id == change.entry_id:
                return f"docs/{Path(function.source_id).with_suffix('.md')}"
    raise ValueError(f"entry {change.entry_id!r} is missing from both contexts")


def produce_results(
    changes: Iterable[EntryChange],
    author: LlmDocumentAuthor,
) -> tuple[tuple[DocumentKey, DocumentResult], ...]:
    """Map code facts to desired fragment states; deletion needs no LLM request."""
    produced: list[tuple[DocumentKey, DocumentResult]] = []
    for change in changes:
        key = document_key_for(change)
        if change.new_entry is None:
            result = DocumentResult(fragment_id_for(change), ResultKind.DELETE)
        else:
            result = author.author(request_for_entry(change))
        produced.append((key, result))
    return tuple(produced)


def publish_results(
    document_root: Path,
    results: Iterable[tuple[DocumentKey, DocumentResult]],
) -> None:
    """Caller-owned grouping, physical I/O, and physical-file deletion policy."""
    by_document: dict[DocumentKey, list[DocumentResult]] = defaultdict(list)
    for key, result in results:
        by_document[key].append(result)

    for key in sorted(by_document):
        path = document_root / key
        markdown = path.read_text(encoding="utf-8") if path.exists() else ""
        for result in by_document[key]:
            markdown = apply_result(markdown, result).markdown

        # This policy deletes a file only when it has no managed fragments and
        # no caller-owned Markdown. Different callers can choose differently.
        if not markdown.strip():
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")


def synchronize(
    source_root: Path,
    document_root: Path,
    tracker: FileTracker,
    author: LlmDocumentAuthor,
) -> None:
    """Analyze one source revision, publish its results, then advance baseline."""
    report = analyze_changes(
        tracker.scan(),
        load_vba_sources(source_root),
        [VbaAnalyzer()],
    )
    changes = entry_changes(report)
    print("Affected entries:", [change.entry_id for change in changes])
    publish_results(document_root, produce_results(changes, author))
    tracker.commit(
        message="documentation synchronized",
        expected_revision=report.working_revision,
        expected_baseline_revision=report.baseline_revision,
    )


def write_source(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        project_root = Path(directory)
        source_root = project_root / "src"
        document_root = project_root / "published"
        source_root.mkdir()
        document_root.mkdir()

        write_source(
            source_root,
            "frmOrder.frm",
            "Private Sub bSave_Click()\n"
            "    Call ValidateOrder\n"
            "End Sub\n",
        )
        write_source(
            source_root,
            "modOrder.bas",
            "Public Sub ValidateOrder()\n"
            "    result = 1\n"
            "End Sub\n",
        )
        tracker = FileTracker(str(source_root))
        tracker.commit(message="initial source baseline")
        author = LlmDocumentAuthor(
            FilePromptRenderer(
                Path(__file__).with_name("system_prompt.txt"),
                Path(__file__).with_name("user_prompt.template"),
            ),
            DemoLlmClient(),
        )

        print("== Generate a fragment after a dependency change ==")
        write_source(
            source_root,
            "modOrder.bas",
            "Public Sub ValidateOrder()\n"
            "    result = 2\n"
            "End Sub\n",
        )
        synchronize(source_root, document_root, tracker, author)
        document = document_root / "docs" / "frmOrder.md"
        print(document.relative_to(document_root))
        print(document.read_text(encoding="utf-8"))

        print("== Remove the Entry and its fragment ==")
        (source_root / "frmOrder.frm").unlink()
        synchronize(source_root, document_root, tracker, author)
        print("Document exists:", document.exists())


if __name__ == "__main__":
    main()
