"""Run a complete caller-owned documentation lifecycle.

This example deliberately keeps document paths, storage, grouping, and baseline
advancement outside the three reusable packages. Run it with:

    uv run python examples/documentation_lifecycle.py
"""

from __future__ import annotations

import tempfile
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from change_analyzer import EntryChange, analyze_changes, entry_changes
from codegraph import SourceFile, VbaAnalyzer
from document_updater import (
    AuthorRequest,
    DocumentAuthor,
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


@dataclass(frozen=True)
class EntryDocumentationPolicy:
    """Map entry-scoped code facts to caller-owned document artifacts."""

    metadata: PromptMetadata

    def fragment_id(self, change: EntryChange) -> str:
        return f"entry:{change.entry_id}"

    def document_key(self, change: EntryChange) -> DocumentKey:
        source_id = self._entry_source_id(change)
        if source_id is None:
            raise ValueError(f"entry {change.entry_id!r} is missing from both contexts")
        return f"docs/{Path(source_id).with_suffix('.md')}"

    def author_request(self, change: EntryChange) -> AuthorRequest:
        return AuthorRequest(
            fragment_id=self.fragment_id(change),
            blocks=(
                PromptBlock("entry_id", change.entry_id),
                PromptBlock("code_context", self._code_context(change)),
                PromptBlock(
                    "function_changes",
                    "\n".join(item.diff for item in change.function_changes),
                ),
            ),
            metadata=self.metadata,
        )

    def result_for(
        self,
        change: EntryChange,
        author: DocumentAuthor,
    ) -> tuple[DocumentKey, DocumentResult]:
        if change.new_entry is None:
            result = DocumentResult(self.fragment_id(change), ResultKind.DELETE)
        else:
            result = author.author(self.author_request(change))
        return self.document_key(change), result

    def _code_context(self, change: EntryChange) -> str:
        return "\n\n".join(
            function.source
            for context in (change.old_context, change.new_context)
            if context is not None
            for function in context.functions
        )

    def _entry_source_id(self, change: EntryChange) -> str | None:
        for context in (change.new_context, change.old_context):
            if context is None:
                continue
            for function in context.functions:
                if function.id == change.entry_id:
                    return function.source_id
        return None


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


@dataclass(frozen=True)
class DocumentPublisher:
    """Apply grouped fragment results and persist the caller's documents."""

    root: Path

    def publish(self, results: Iterable[tuple[DocumentKey, DocumentResult]]) -> None:
        by_document: dict[DocumentKey, list[DocumentResult]] = defaultdict(list)
        for key, result in results:
            by_document[key].append(result)

        for key in sorted(by_document):
            self._publish_document(key, by_document[key])

    def _publish_document(
        self,
        key: DocumentKey,
        results: Iterable[DocumentResult],
    ) -> None:
        path = self.root / key
        markdown = path.read_text(encoding="utf-8") if path.exists() else ""
        for result in results:
            markdown = apply_result(markdown, result).markdown

        # Delete only empty managed documents; caller-owned Markdown is retained.
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
    policy = EntryDocumentationPolicy(
        metadata=PromptMetadata(
            project="Order System",
            language="VBA",
            glossary=(
                "- Entry: a documentation entry point.\n"
                "- Fragment: a managed Markdown section."
            ),
        ),
    )
    results = tuple(policy.result_for(change, author) for change in changes)
    DocumentPublisher(document_root).publish(results)
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
