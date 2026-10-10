"""Caller-owned, persistent Form-level documentation checkpoints."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from documentation_example_support import (
    affected_entries_for_form,
    document_key_for_form,
    DocumentPublisher,
    form_source_ids_to_analyze,
    LlmClient,
    publish_documents_for_forms,
)
from filetracker import FileTracker, RevisionConflictError
from progress_checkpoint import ProgressCheckpoint


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _file_digest(path: Path) -> str:
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        return "absent"
    return hashlib.sha256(content).hexdigest()


def _shared_inputs(source_root: Path) -> tuple[tuple[str, str], ...]:
    prompts = Path(__file__).with_name("prompts")
    paths = (
        tuple(sorted(source_root.glob("Module_*.txt")))
        + tuple(sorted(path for path in (prompts / "system").iterdir() if path.is_file()))
        + tuple(sorted((prompts / "user").glob("[0-9][0-9]-*.user.template")))
    )
    return tuple((str(path.resolve()), _file_digest(path)) for path in paths)


def run_documentation(
    source_root: Path,
    document_root: Path,
    progress_path: Path,
    llm: LlmClient,
    *,
    generation_version: str = "demo-v1",
) -> tuple[str, ...]:
    """Resume successful Forms; commit the baseline only after all Forms succeed.

    Use one writer per project. Change generation_version when the model,
    generation settings, or analysis implementation changes.
    """
    if progress_path.resolve().is_relative_to(source_root.resolve()):
        raise ValueError("progress_path must be outside source_root")
    if document_root.resolve().is_relative_to(source_root.resolve()):
        raise ValueError("document_root must be outside source_root")

    tracker = FileTracker(str(source_root))
    change_set = tracker.scan()
    publisher = DocumentPublisher(document_root)
    shared_inputs = _shared_inputs(source_root)
    progress = ProgressCheckpoint(progress_path)
    updated_keys: list[str] = []

    for form_id in form_source_ids_to_analyze(source_root, change_set):
        document_key = document_key_for_form(form_id)
        document_path = document_root / document_key
        form_digest = _file_digest(source_root / form_id)
        input_digest = _digest((
            str(source_root.resolve()),
            str(document_root.resolve()),
            change_set.baseline_revision,
            generation_version,
            shared_inputs,
            form_id,
            form_digest,
        ))
        if progress.matches(form_id, input_digest, _file_digest(document_path)):
            print(f"Skipped completed Form: {form_id}")
            continue

        changes = affected_entries_for_form(source_root, change_set, form_id)
        updated_keys.extend(publish_documents_for_forms(
            {form_id: changes}, publisher, llm,
        ))
        # Do not certify output generated while its inputs were changing.
        if (
            _file_digest(source_root / form_id) != form_digest
            or _shared_inputs(source_root) != shared_inputs
        ):
            raise RevisionConflictError(
                f"documentation inputs changed while processing {form_id!r}"
            )
        progress.record_success(form_id, input_digest, _file_digest(document_path))
        print(f"Completed Form: {form_id}")

    tracker.commit(
        message="documentation published",
        expected_revision=change_set.working_revision,
        expected_baseline_revision=change_set.baseline_revision,
    )
    progress.clear()
    return tuple(updated_keys)
