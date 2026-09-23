"""Stable support objects shared by the documentation example scripts."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from change_analyzer import EntryChange, analyze_changes, entry_changes
from codegraph import SourceFile, VbaAnalyzer
from document_updater import DocumentResult, ResultKind, apply_result, get_fragment_markdown
from filetracker import ChangeSet, ContentSnapshot, FileChange


_FORM_FILE_GLOB = "Form_*.txt"
_MODULE_FILE_GLOB = "Module_*.txt"
_FORM_FILE_PREFIX = "form_"
_MODULE_FILE_PREFIX = "module_"
_VBA_EXPORT_SUFFIX = ".txt"
_CODE_BEHIND_FORM_MARKER = "CodeBehindForm"
_DOCUMENT_DIRECTORY = "docs"
_PROMPT_FILE_PAIRS = (
    ("business_flow.system.txt", "entry.user.template"),
    ("boundaries.system.txt", "entry.user.template"),
)


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
            code = user_prompt.split("<Code>\n", 1)[1].split("\n</Code>", 1)[0]
            implementation_line = next(
                (
                    line.strip()
                    for line in code.splitlines()
                    if line.strip().startswith("result =")
                    or line.strip().casefold().startswith("insert into ")
                ),
                "No result is assigned.",
            )
            return (
                f"## {entry_id.rsplit(':', 1)[-1]}\n\n"
                f"This generated fragment documents `{entry_id}`.\n\n"
                f"Current implementation: `{implementation_line}`."
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

    prompts: list[tuple[str, str]] = []
    for system_file, user_file in _PROMPT_FILE_PAIRS:
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


# Form impact analysis


def form_source_ids_to_analyze(
    source_root: Path,
    change_set: ChangeSet,
) -> tuple[str, ...]:
    """Choose Forms affected directly or by a shared Module change."""
    form_source_ids = {
        change.path.as_posix()
        for change in change_set.files
        if _is_form_source(change.path.as_posix())
    }
    if any(
        _is_module_source(change.path.as_posix())
        for change in change_set.files
    ):
        form_source_ids.update(
            path.name
            for path in source_root.glob(_FORM_FILE_GLOB)
            if path.is_file()
        )
    return tuple(sorted(form_source_ids))


def affected_entries_for_form(
    source_root: Path,
    change_set: ChangeSet,
    form_source_id: str,
) -> tuple[EntryChange, ...]:
    """Analyze one Form with every current or deleted shared Module."""
    context_changes = ChangeSet(
        files=tuple(
            _vba_file_change(change)
            for change in change_set.files
            if change.path.as_posix() == form_source_id
            or _is_module_source(change.path.as_posix())
        ),
        baseline_revision=change_set.baseline_revision,
        working_revision=change_set.working_revision,
    )
    if not context_changes.has_changes:
        return ()

    working_paths = list(sorted(source_root.glob(_MODULE_FILE_GLOB)))
    form_path = source_root / form_source_id
    if form_path.is_file():
        working_paths.append(form_path)
    working_sources = tuple(
        SourceFile(
            source_id=path.name,
            content=_read_vba_source(path),
            language="vba",
        )
        for path in sorted(working_paths)
    )
    source_languages = {
        change.path.as_posix(): "vba"
        for change in context_changes.files
    }
    return entry_changes(
        analyze_changes(
            context_changes,
            working_sources,
            [VbaAnalyzer()],
            source_languages=source_languages,
        )
    )


def affected_entries_by_form(
    source_root: Path,
    change_set: ChangeSet,
) -> dict[str, tuple[EntryChange, ...]]:
    """Collect non-empty Entry changes for each Form affected by this scan."""
    changes_by_form = {}
    for form_source_id in form_source_ids_to_analyze(source_root, change_set):
        changes = affected_entries_for_form(
            source_root,
            change_set,
            form_source_id,
        )
        if changes:
            changes_by_form[form_source_id] = changes
    return changes_by_form


# VBA export adaptation


def _read_vba_source(path: Path) -> str:
    data = path.read_bytes()
    text = _decode_file_bytes(data)
    return _vba_text_for_analysis(path.name, text)


def _vba_file_change(change: FileChange) -> FileChange:
    source_id = change.path.as_posix()
    return FileChange(
        path=change.path,
        status=change.status,
        baseline_state=change.baseline_state,
        working_state=change.working_state,
        baseline_content=_vba_content_snapshot(
            source_id,
            change.baseline_content,
        ),
        working_content=_vba_content_snapshot(
            source_id,
            change.working_content,
        ),
    )


def _decode_file_bytes(data: bytes) -> str:
    """Robustly decode source bytes across UTF-16, UTF-8 (with or without BOM), and ANSI."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return data.decode("utf-16")
        except UnicodeDecodeError:
            pass
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data.decode("utf-8-sig")
        except UnicodeDecodeError:
            pass

    for encoding in ("utf-8", "utf-16", "utf-16-le", "gbk", "cp1252"):
        try:
            return data.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", errors="replace")


def _vba_content_snapshot(
    source_id: str,
    snapshot: ContentSnapshot,
) -> ContentSnapshot:
    if not snapshot.is_text:
        return snapshot
    assert snapshot.text is not None
    return ContentSnapshot(
        availability=snapshot.availability,
        text=_vba_text_for_analysis(source_id, snapshot.text),
    )


def _vba_text_for_analysis(source_id: str, text: str) -> str:
    text = text.removeprefix("\ufeff")
    if not _is_form_source(source_id):
        return text

    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.strip() == _CODE_BEHIND_FORM_MARKER:
            return "".join(lines[index + 1 :])
    return ""


def _is_form_source(source_id: str) -> bool:
    path = Path(source_id)
    return (
        path.parent == Path(".")
        and path.suffix.casefold() == _VBA_EXPORT_SUFFIX
        and path.name.casefold().startswith(_FORM_FILE_PREFIX)
    )


def _is_module_source(source_id: str) -> bool:
    path = Path(source_id)
    return (
        path.parent == Path(".")
        and path.suffix.casefold() == _VBA_EXPORT_SUFFIX
        and path.name.casefold().startswith(_MODULE_FILE_PREFIX)
    )


# Document publication


@dataclass(frozen=True)
class DocumentPublisher:
    """Persist one form document's fragment states."""

    root: Path

    def read_fragment(self, document_key: str, fragment_id: str) -> str | None:
        """Read one fragment without knowing which producer owns it."""
        path = self.root / document_key
        markdown = path.read_text(encoding="utf-8") if path.exists() else ""
        return get_fragment_markdown(markdown, fragment_id)

    def publish(
        self,
        document_key: str,
        results: Iterable[DocumentResult],
    ) -> bool:
        """Apply a form's results and report whether its document changed."""
        results = tuple(results)
        if len({result.fragment_id for result in results}) != len(results):
            raise ValueError(
                f"duplicate fragment result for document {document_key!r}"
            )

        path = self.root / document_key
        markdown = path.read_text(encoding="utf-8") if path.exists() else ""
        changed = False
        for result in results:
            updated_markdown = apply_result(markdown, result)
            changed = changed or updated_markdown != markdown
            markdown = updated_markdown

        if not markdown.strip():
            changed = changed or path.exists()
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(markdown, encoding="utf-8")
        return changed


def document_results_for_entries(
    changes: Iterable[EntryChange],
    document_key: str,
    publisher: DocumentPublisher,
    llm: LlmClient,
) -> tuple[DocumentResult, ...]:
    """Generate one form document's fragments from its affected Entries."""
    return tuple(
        generate_entry_document(
            change,
            fragment_id=f"entry:{change.entry_id}",
            existing_markdown=publisher.read_fragment(
                document_key,
                f"entry:{change.entry_id}",
            ),
            llm=llm,
        )
        for change in changes
    )


def publish_documents_for_forms(
    changes_by_form: Mapping[str, Iterable[EntryChange]],
    publisher: DocumentPublisher,
    llm: LlmClient,
) -> tuple[str, ...]:
    """Generate and publish each affected Form's Entry documentation."""
    updated_document_keys = []
    for form_source_id, changes in sorted(changes_by_form.items()):
        document_key = document_key_for_form(form_source_id)
        results = document_results_for_entries(
            changes,
            document_key,
            publisher,
            llm,
        )
        if publisher.publish(document_key, results):
            updated_document_keys.append(document_key)
    return tuple(updated_document_keys)


def document_key_for_form(form_source_id: str) -> str:
    """Map a form source ID to the document owned by that form."""
    return f"{_DOCUMENT_DIRECTORY}/{Path(form_source_id).with_suffix('.md').as_posix()}"


def write_source(
    root: Path,
    name: str,
    content: str,
    *,
    encoding: str = "utf-8",
) -> None:
    """Write a source file used by an example setup."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding=encoding)
