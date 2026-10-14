"""Stable support objects shared by the documentation example scripts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from change_analyzer import EntryChange, FunctionChange, analyze_changes, entry_changes
from codegraph import SourceFile, VbaAnalyzer
from document_updater import DocumentResult, ResultKind, get_fragment_markdown
from filetracker import ChangeSet, ContentSnapshot, FileChange


_FORM_FILE_GLOB = "Form_*.txt"
_MODULE_FILE_GLOB = "Module_*.txt"
_FORM_FILE_PREFIX = "form_"
_MODULE_FILE_PREFIX = "module_"
_VBA_EXPORT_SUFFIX = ".txt"
_CODE_BEHIND_FORM_MARKER = "CodeBehindForm"
_DOCUMENT_DIRECTORY = "docs"


class LlmClient(Protocol):
    """Stable adapter boundary for the changeable text-generation provider."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        ...


@dataclass(frozen=True)
class DemoLlmClient:
    """A deterministic stand-in for a real LLM provider adapter."""

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        identifier_tag = "Function" if "<Function>\n" in user_prompt else "Entry"
        identifier = user_prompt.split(
            f"<{identifier_tag}>\n", 1
        )[1].split(f"\n</{identifier_tag}>", 1)[0]
        if user_prompt.startswith("Extract the business flow."):
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
                f"## {identifier.rsplit(':', 1)[-1]}\n\n"
                f"This generated fragment documents `{identifier}`.\n\n"
                f"Current implementation: `{implementation_line}`."
            )
        return "### Boundaries\n\nValidation failures prevent the operation."


@dataclass(frozen=True)
class PromptTemplates:
    system_prompt: str
    user_templates: tuple[str, ...]


def _load_prompt_templates(
    system_directory: Path,
    user_directory: Path,
) -> PromptTemplates:
    if system_directory.resolve() == user_directory.resolve():
        raise ValueError("system and user prompt directories must differ")

    system_files = sorted(path for path in system_directory.iterdir() if path.is_file())
    if len(system_files) != 1:
        raise ValueError(
            f"expected exactly one system prompt file in {system_directory}, "
            f"found {len(system_files)}"
        )
    user_files = sorted(user_directory.glob("[0-9][0-9]-*.user.template"))
    if not user_files:
        raise ValueError(f"no numbered user prompt templates in {user_directory}")

    system_prompt = system_files[0].read_text(encoding="utf-8").strip()
    if not system_prompt:
        raise ValueError(f"empty prompt file: {system_files[0]}")
    templates = []
    for user_file in user_files:
        template = user_file.read_text(encoding="utf-8")
        if not template.strip():
            raise ValueError(f"empty prompt file: {user_file}")
        templates.append(template)
    return PromptTemplates(system_prompt, tuple(templates))


def generate_entry_document(
    change: EntryChange,
    *,
    fragment_id: str,
    existing_markdown: str | None,
    llm: LlmClient,
    system_directory: Path = Path(__file__).with_name("prompts") / "system",
    user_directory: Path = Path(__file__).with_name("prompts") / "user",
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

    prompt_templates = _load_prompt_templates(
        system_directory, user_directory
    )
    user_prompts = tuple(
        template.format(entry_id=change.entry_id, code=code, history=history)
        for template in prompt_templates.user_templates
    )

    completions: list[str] = []
    for user_prompt in user_prompts:
        completion = llm.generate(prompt_templates.system_prompt, user_prompt).strip()
        if not completion:
            raise ValueError(f"empty LLM response for entry {change.entry_id!r}")
        completions.append(completion)
    return DocumentResult(
        fragment_id, ResultKind.UPSERT, "\n\n".join(completions)
    )


def generate_function_document(
    change: FunctionChange,
    *,
    fragment_id: str,
    existing_markdown: str | None,
    llm: LlmClient,
    system_directory: Path = (
        Path(__file__).with_name("prompts") / "function" / "system"
    ),
    user_directory: Path = (
        Path(__file__).with_name("prompts") / "function" / "user"
    ),
) -> DocumentResult:
    """Generate one function fragment, or delete it when the function was removed."""
    function = change.new_function
    if function is None:
        return DocumentResult(fragment_id, ResultKind.DELETE)

    history = ""
    if existing_markdown is not None:
        history = (
            f"\n\n<ExistingFragment>\n{existing_markdown}\n</ExistingFragment>"
            f"\n\n<ChangeSummary>\n{change.diff}\n</ChangeSummary>"
        )

    prompt_templates = _load_prompt_templates(system_directory, user_directory)
    user_prompts = tuple(
        template.format(
            function_id=change.function_id,
            code=function.source,
            history=history,
        )
        for template in prompt_templates.user_templates
    )

    completions: list[str] = []
    for user_prompt in user_prompts:
        completion = llm.generate(prompt_templates.system_prompt, user_prompt).strip()
        if not completion:
            raise ValueError(
                f"empty LLM response for function {change.function_id!r}"
            )
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
    return _affected_entries_for_form(
        source_root,
        change_set,
        form_source_id,
        analyzer=VbaAnalyzer(cache_size=4096),
    )


def _affected_entries_for_form(
    source_root: Path,
    change_set: ChangeSet,
    form_source_id: str,
    *,
    analyzer: VbaAnalyzer,
    module_sources: tuple[SourceFile, ...] | None = None,
) -> tuple[EntryChange, ...]:
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

    if module_sources is None:
        module_sources = _working_module_sources(source_root)
    working_sources = list(module_sources)
    form_path = source_root / form_source_id
    if form_path.is_file():
        working_sources.append(
            SourceFile(
                source_id=form_source_id,
                content=_read_vba_source(form_path),
                language="vba",
            )
        )
    source_languages = {
        change.path.as_posix(): "vba"
        for change in context_changes.files
    }
    return entry_changes(
        analyze_changes(
            context_changes,
            working_sources,
            [analyzer],
            source_languages=source_languages,
        )
    )


def affected_entries_by_form(
    source_root: Path,
    change_set: ChangeSet,
) -> dict[str, tuple[EntryChange, ...]]:
    """Analyze isolated Forms, sharing Module reads and syntax facts per scan."""
    form_source_ids = form_source_ids_to_analyze(source_root, change_set)
    if not form_source_ids:
        return {}
    module_sources = _working_module_sources(source_root)
    analyzer = VbaAnalyzer(cache_size=4096)
    changes_by_form = {}
    for form_source_id in form_source_ids:
        changes = _affected_entries_for_form(
            source_root,
            change_set,
            form_source_id,
            analyzer=analyzer,
            module_sources=module_sources,
        )
        if changes:
            changes_by_form[form_source_id] = changes
    return changes_by_form


def _working_module_sources(source_root: Path) -> tuple[SourceFile, ...]:
    return tuple(
        SourceFile(
            source_id=path.name,
            content=_read_vba_source(path),
            language="vba",
        )
        for path in sorted(source_root.glob(_MODULE_FILE_GLOB))
    )


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


def document_results_for_entries(
    changes: Iterable[EntryChange],
    existing_markdown: str | None,
    llm: LlmClient,
) -> tuple[DocumentResult, ...]:
    """Generate fragments using only the caller-captured document contents."""
    return tuple(
        generate_entry_document(
            change,
            fragment_id=f"entry:{change.entry_id}",
            existing_markdown=(
                get_fragment_markdown(existing_markdown, f"entry:{change.entry_id}")
                if existing_markdown is not None
                else None
            ),
            llm=llm,
        )
        for change in changes
    )


def document_results_for_functions(
    changes: Iterable[FunctionChange],
    existing_markdown: str | None,
    llm: LlmClient,
) -> tuple[DocumentResult, ...]:
    """Generate function fragments from caller-captured Markdown and code facts."""
    results = []
    for change in changes:
        fragment_id = f"function:{change.function_id}"
        results.append(
            generate_function_document(
                change,
                fragment_id=fragment_id,
                existing_markdown=(
                    get_fragment_markdown(existing_markdown, fragment_id)
                    if existing_markdown is not None
                    else None
                ),
                llm=llm,
            )
        )
    return tuple(results)


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
