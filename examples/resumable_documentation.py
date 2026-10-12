"""Stage complete Form documents before resumable, single-writer publication."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from documentation_example_support import (
    affected_entries_for_form,
    document_key_for_form,
    document_results_for_entries,
    DocumentPublisher,
    form_source_ids_to_analyze,
    LlmClient,
)
from document_updater import apply_result
from filetracker import FileTracker, RevisionConflictError
from progress_checkpoint import atomic_write_text


@dataclass(frozen=True)
class _FormResult:
    form_id: str
    document_key: str
    input_digest: str
    before_digest: str
    output_digest: str


@dataclass(frozen=True)
class _Batch:
    baseline_revision: str
    working_revision: str
    phase: str = "generating"
    records: tuple[_FormResult, ...] = ()


def _payload_path(stage: Path, form_id: str) -> Path:
    return stage / f"{_digest(form_id)}.md"


def _valid_digest(value: object, *, allow_absent: bool = False) -> bool:
    return isinstance(value, str) and (
        (allow_absent and value == "absent")
        or (len(value) == 64 and all(char in "0123456789abcdef" for char in value))
    )


def _load_batch(stage: Path) -> _Batch | None:
    path = stage / "manifest.json"
    if stage.is_symlink() or path.is_symlink():
        raise ValueError("staging paths must not be symbolic links")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if (
        not isinstance(data, dict)
        or set(data) != {
            "version", "baseline_revision", "working_revision", "phase", "records",
        }
        or type(data["version"]) is not int
        or data["version"] != 1
        or not isinstance(data["baseline_revision"], str)
        or not isinstance(data["working_revision"], str)
        or data["phase"] not in ("generating", "publishing", "published")
        or not isinstance(data["records"], list)
    ):
        raise ValueError(f"invalid staging manifest: {path}")
    records = []
    for item in data["records"]:
        if (
            not isinstance(item, dict)
            or set(item) != {
                "form_id", "document_key", "input_digest",
                "before_digest", "output_digest",
            }
            or not isinstance(item["form_id"], str)
            or Path(item["form_id"]).name != item["form_id"]
            or "\\" in item["form_id"]
            or not item["form_id"].casefold().startswith("form_")
            or not item["form_id"].casefold().endswith(".txt")
            or item["document_key"] != document_key_for_form(item["form_id"])
            or not _valid_digest(item["input_digest"])
            or not _valid_digest(item["before_digest"], allow_absent=True)
            or not _valid_digest(item["output_digest"], allow_absent=True)
        ):
            raise ValueError(f"invalid staged Form record: {path}")
        records.append(_FormResult(**item))
    if len({record.form_id for record in records}) != len(records):
        raise ValueError(f"duplicate staged Form record: {path}")
    return _Batch(
        data["baseline_revision"], data["working_revision"],
        data["phase"], tuple(sorted(records, key=lambda record: record.form_id)),
    )


def _save_batch(stage: Path, batch: _Batch) -> None:
    atomic_write_text(
        stage / "manifest.json",
        json.dumps({"version": 1, **asdict(batch)}, sort_keys=True, indent=2) + "\n",
    )


def _clear_stage(stage: Path) -> None:
    # Recognize atomic-write leftovers as well as certified and orphaned payloads.
    artifacts = []
    for path in sorted(stage.iterdir()):
        owned = (
            path.name == "manifest.json"
            or (path.suffix == ".md" and _valid_digest(path.stem))
            or re.fullmatch(
                r"\.(?:manifest\.json|[0-9a-f]{64}\.md)\.[a-z0-9_]+\.tmp",
                path.name,
            ) is not None
        )
        if not owned or path.is_symlink() or not path.is_file():
            raise ValueError(f"unexpected staging file: {path}")
        if path.name != "manifest.json":
            artifacts.append(path)
    for path in artifacts:
        path.unlink()
    (stage / "manifest.json").unlink(missing_ok=True)
    stage.rmdir()


def _staged_digest(stage: Path, record: _FormResult) -> str:
    path = _payload_path(stage, record.form_id)
    if path.is_symlink():
        raise ValueError(f"staged document must not be a symbolic link: {path}")
    return _file_digest(path)


def _check_document(
    document_root: Path, record: _FormResult, *, allow_output: bool = False,
) -> None:
    allowed = {record.before_digest}
    if allow_output:
        allowed.add(record.output_digest)
    if _file_digest(document_root / record.document_key) not in allowed:
        raise RevisionConflictError(
            f"document changed while processing {record.form_id!r}"
        )


def _publish_staged(
    stage: Path, document_root: Path, record: _FormResult,
) -> bool:
    _check_document(document_root, record, allow_output=True)
    if _staged_digest(stage, record) != record.output_digest:
        raise RevisionConflictError(f"staged result changed for {record.form_id!r}")
    path = document_root / record.document_key
    if _file_digest(path) == record.output_digest:
        return False
    if record.output_digest == "absent":
        path.unlink(missing_ok=True)
    else:
        content = _payload_path(stage, record.form_id).read_bytes()
        if hashlib.sha256(content).hexdigest() != record.output_digest:
            raise RevisionConflictError(f"staged result changed for {record.form_id!r}")
        atomic_write_text(path, content.decode("utf-8"))
    return True


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
    llm: LlmClient,
    *,
    generation_version: str = "demo-v1",
) -> tuple[str, ...]:
    """Stage every Form, publish all documents, commit, then remove staging.

    Use one writer per project. Change generation_version when the model,
    generation settings, or analysis implementation changes. Publication is
    atomic per file, not per batch; failures retain staged results for retry.
    """
    if document_root.resolve().is_relative_to(source_root.resolve()):
        raise ValueError("document_root must be outside source_root")

    tracker = FileTracker(str(source_root))
    change_set = tracker.scan()
    stage = document_root / ".resumable"
    previous = _load_batch(stage)
    if previous is not None and previous.baseline_revision != change_set.baseline_revision:
        if (
            previous.phase == "published"
            and not change_set.has_changes
            and previous.working_revision == change_set.working_revision
        ):
            for record in previous.records:
                if _file_digest(document_root / record.document_key) != record.output_digest:
                    raise RevisionConflictError(
                        f"published document changed before cleanup: {record.document_key}"
                    )
            _clear_stage(stage)
            return ()
        print("Invalidated staged batch: source baseline changed")
        previous = None
    if not change_set.has_changes:
        if stage.exists():
            _clear_stage(stage)
        return ()

    publisher = DocumentPublisher(document_root)
    shared_inputs = _shared_inputs(source_root)
    form_ids = form_source_ids_to_analyze(source_root, change_set)
    form_digests = {
        form_id: _file_digest(source_root / form_id) for form_id in form_ids
    }
    input_digests = {
        form_id: _digest((
            str(source_root.resolve()),
            str(document_root.resolve()),
            change_set.baseline_revision,
            generation_version,
            shared_inputs,
            form_id,
            form_digests[form_id],
        ))
        for form_id in form_ids
    }
    reusable = []
    for record in previous.records if previous is not None else ():
        current_digest = _file_digest(document_root / record.document_key)
        allowed = {record.before_digest}
        if previous is not None and previous.phase != "generating":
            allowed.add(record.output_digest)
        if (
            input_digests.get(record.form_id) == record.input_digest
            and _staged_digest(stage, record) == record.output_digest
            and current_digest in allowed
        ):
            reusable.append(replace(record, before_digest=current_digest))
        else:
            print(f"Invalidated staged Form: {record.form_id}")

    batch = _Batch(
        change_set.baseline_revision, change_set.working_revision,
        records=tuple(reusable),
    )
    _save_batch(stage, batch)
    for form_id in form_ids:
        if any(record.form_id == form_id for record in batch.records):
            print(f"Skipped completed Form: {form_id}")
            continue

        document_key = document_key_for_form(form_id)
        document_path = document_root / document_key
        before_digest = _file_digest(document_path)
        markdown = (
            document_path.read_text(encoding="utf-8")
            if before_digest != "absent" else ""
        )
        changes = affected_entries_for_form(source_root, change_set, form_id)
        results = document_results_for_entries(
            changes, document_key, publisher, llm,
        )
        if len({result.fragment_id for result in results}) != len(results):
            raise ValueError(f"duplicate fragment result for document {document_key!r}")
        for result in results:
            markdown = apply_result(markdown, result)
        if (
            _file_digest(source_root / form_id) != form_digests[form_id]
            or _shared_inputs(source_root) != shared_inputs
            or _file_digest(document_path) != before_digest
        ):
            raise RevisionConflictError(
                f"documentation inputs changed while processing {form_id!r}"
            )
        payload = _payload_path(stage, form_id)
        if markdown.strip():
            atomic_write_text(payload, markdown)
            output_digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        else:
            payload.unlink(missing_ok=True)
            output_digest = "absent"
        record = _FormResult(
            form_id, document_key, input_digests[form_id],
            before_digest, output_digest,
        )
        batch = replace(
            batch, records=tuple(sorted(
                (*batch.records, record), key=lambda item: item.form_id,
            )),
        )
        _save_batch(stage, batch)
        print(f"Staged Form: {form_id}")

    current = tracker.scan()
    if (
        current.baseline_revision != change_set.baseline_revision
        or current.working_revision != change_set.working_revision
        or _shared_inputs(source_root) != shared_inputs
    ):
        raise RevisionConflictError("documentation inputs changed before publication")
    for record in batch.records:
        _check_document(document_root, record)
        if _staged_digest(stage, record) != record.output_digest:
            raise RevisionConflictError(f"staged result changed for {record.form_id!r}")
    batch = replace(batch, phase="publishing")
    _save_batch(stage, batch)
    updated_keys = tuple(
        record.document_key for record in batch.records
        if _publish_staged(stage, document_root, record)
    )
    for record in batch.records:
        if _file_digest(document_root / record.document_key) != record.output_digest:
            raise RevisionConflictError(f"published document changed: {record.document_key}")
    if _shared_inputs(source_root) != shared_inputs:
        raise RevisionConflictError("documentation inputs changed during publication")
    batch = replace(batch, phase="published")
    _save_batch(stage, batch)

    tracker.commit(
        message="documentation published",
        expected_revision=change_set.working_revision,
        expected_baseline_revision=change_set.baseline_revision,
    )
    _clear_stage(stage)
    return updated_keys
