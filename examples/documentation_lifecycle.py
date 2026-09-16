"""Run a caller-owned documentation lifecycle.

The reusable document_updater package only owns managed Markdown fragments.
This example owns Entry mapping, prompts, LLM calls, document paths, and I/O.

Run with:

    uv run python examples/documentation_lifecycle.py
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from change_analyzer import EntryChange, ImpactReport, analyze_changes, entry_changes
from codegraph import SourceFile, VbaAnalyzer
from document_updater import DocumentResult, ResultKind, apply_result, get_fragment_markdown
from filetracker import FileTracker


class LlmClient(Protocol):
    """Stable adapter boundary for the changeable text-generation provider."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        ...


@dataclass(frozen=True)
class DemoLlmClient:
    """A deterministic stand-in for a real LLM provider adapter."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        entry_id = user_prompt.split("<Entry>\n", 1)[1].split("\n</Entry>", 1)[0]
        if system_prompt == "Extract the business flow.":
            return (
                f"## {entry_id.rsplit(':', 1)[-1]}\n\n"
                f"This generated fragment documents `{entry_id}`."
            )
        return "### Boundaries\n\nValidation failures prevent the operation."


def generate_entry_document(
    change: EntryChange,
    *,
    fragment_id: str,
    existing_markdown: str | None,
    llm: LlmClient,
    prompt_directory: Path = Path(__file__).with_name("prompts"),
) -> DocumentResult:
    """Read prompt files, call the LLM in order, and merge one Entry fragment."""
    if change.new_entry is None:
        return DocumentResult(fragment_id, ResultKind.DELETE)
    if change.new_context is None:
        raise ValueError(
            f"existing entry {change.entry_id!r} is missing new context"
        )

    code = "\n\n".join(
        function.source for function in change.new_context.functions
    )
    history = ""
    if existing_markdown is not None:
        summary = "\n".join(item.diff for item in change.function_changes)
        history = (
            f"\n\n<ExistingFragment>\n{existing_markdown}\n</ExistingFragment>"
            f"\n\n<ChangeSummary>\n{summary}\n</ChangeSummary>"
        )

    # This ordered list is caller policy, not a reusable prompt pipeline.
    prompt_files = (
        ("business_flow.system.txt", "entry.user.template"),
        ("boundaries.system.txt", "entry.user.template"),
    )
    prompts: list[tuple[str, str]] = []
    for system_file, user_file in prompt_files:
        system_prompt = (prompt_directory / system_file).read_text(
            encoding="utf-8"
        ).strip()
        template = (prompt_directory / user_file).read_text(encoding="utf-8")
        if not system_prompt or not template.strip():
            raise ValueError(f"empty prompt file in pair {system_file!r}, {user_file!r}")
        user_prompt = template.format(
            entry_id=change.entry_id,
            code=code,
            history=history,
        )
        prompts.append((system_prompt, user_prompt))

    completions: list[str] = []
    for system_prompt, user_prompt in prompts:
        completion = llm.generate(system_prompt, user_prompt).strip()
        if not completion:
            raise ValueError(f"empty LLM response for entry {change.entry_id!r}")
        completions.append(completion)
    return DocumentResult(
        fragment_id, ResultKind.UPSERT, "\n\n".join(completions)
    )


def generate_ui_document(
    source: SourceFile,
    *,
    fragment_id: str,
) -> DocumentResult:
    """Caller extension: extract a component table from complete UI source, no LLM."""
    raise NotImplementedError("UI component extraction must be supplied by the caller")


def entry_document_key(change: EntryChange) -> str:
    """Example routing policy, including the old location of a deleted Entry."""
    for context in (change.new_context, change.old_context):
        if context is None:
            continue
        for function in context.functions:
            if function.id == change.entry_id:
                return f"docs/{Path(function.source_id).with_suffix('.md').as_posix()}"
    raise ValueError(f"entry {change.entry_id!r} is missing from both contexts")


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
    """Persist caller-grouped fragment states to caller-selected documents."""

    root: Path

    def read_fragment(self, document_key: str, fragment_id: str) -> str | None:
        """Read one fragment without knowing which producer owns it."""
        path = self.root / document_key
        markdown = path.read_text(encoding="utf-8") if path.exists() else ""
        return get_fragment_markdown(markdown, fragment_id)

    def publish(
        self,
        results_by_document: Mapping[str, Iterable[DocumentResult]],
    ) -> tuple[str, ...]:
        """Apply results and return the document keys whose content changed."""
        grouped = {
            key: tuple(results)
            for key, results in results_by_document.items()
        }
        self._validate_unique_fragments(grouped)
        return tuple(
            key
            for key in sorted(grouped)
            if self._publish_document(key, grouped[key])
        )

    def _validate_unique_fragments(
        self,
        results_by_document: Mapping[str, tuple[DocumentResult, ...]],
    ) -> None:
        for key, results in results_by_document.items():
            if len({result.fragment_id for result in results}) != len(results):
                raise ValueError(f"duplicate fragment result for document {key!r}")

    def _publish_document(self, key: str, results: Iterable[DocumentResult]) -> bool:
        path = self.root / key
        markdown = path.read_text(encoding="utf-8") if path.exists() else ""
        changed = False
        for result in results:
            updated_markdown = apply_result(markdown, result)
            changed = changed or updated_markdown != markdown
            markdown = updated_markdown

        # Delete only empty managed documents; caller-owned Markdown is retained.
        if not markdown.strip():
            changed = changed or path.exists()
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")
        return changed


def synchronize(
    source_root: Path,
    tracker: FileTracker,
    publisher: DocumentPublisher,
    llm: LlmClient,
    *,
    additional_results: Mapping[str, Iterable[DocumentResult]] | None = None,
) -> ImpactReport:
    """Publish Entry and caller-generated results, then advance the source baseline."""
    report = analyze_changes(
        tracker.scan(),
        load_vba_sources(source_root),
        [VbaAnalyzer()],
    )
    changes = entry_changes(report)
    print("Affected entries:", [change.entry_id for change in changes])

    results_by_document: dict[str, list[DocumentResult]] = {
        key: list(results)
        for key, results in (
            additional_results.items() if additional_results is not None else ()
        )
    }
    for change in changes:
        document_key = entry_document_key(change)
        fragment_id = f"entry:{change.entry_id}"
        result = generate_entry_document(
            change,
            fragment_id=fragment_id,
            existing_markdown=publisher.read_fragment(document_key, fragment_id),
            llm=llm,
        )
        results_by_document.setdefault(document_key, []).append(result)

    print("Affected documents:", sorted(results_by_document))
    changed_documents = publisher.publish(results_by_document)
    print("Changed documents:", list(changed_documents))

    # The baseline moves only after every generated document was published.
    tracker.commit(
        message="documentation synchronized",
        expected_revision=report.working_revision,
        expected_baseline_revision=report.baseline_revision,
    )
    return report


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
        client = DemoLlmClient()
        publisher = DocumentPublisher(document_root)

        print("== Generate documentation from the initial source snapshot ==")
        synchronize(source_root, tracker, publisher, client)
        document = document_root / "docs" / "frmOrder.md"
        print(document.relative_to(document_root))
        print(document.read_text(encoding="utf-8"))

        print("== Update documentation after a dependency change ==")
        write_source(
            source_root,
            "modOrder.bas",
            "Public Sub ValidateOrder()\n"
            "    result = 2\n"
            "End Sub\n",
        )
        synchronize(source_root, tracker, publisher, client)
        print(document.relative_to(document_root))
        print(document.read_text(encoding="utf-8"))

        print("== Remove the Entry and its fragment ==")
        (source_root / "frmOrder.frm").unlink()
        synchronize(source_root, tracker, publisher, client)
        print("Document exists:", document.exists())


if __name__ == "__main__":
    main()
