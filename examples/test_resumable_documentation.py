from pathlib import Path
import json
from unittest.mock import Mock

import pytest

import progress_checkpoint as checkpoint
import resumable_documentation as resumable
from documentation_example_support import DemoLlmClient, write_source
from document_updater import DocumentResult, ResultKind, apply_result
from filetracker import FileTracker, RevisionConflictError


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path]:
    source_root = tmp_path / "src"
    for number in range(1, 4):
        write_source(
            source_root, f"Form_{number}.txt",
            "CodeBehindForm\nPrivate Sub Save_Click()\n"
            "    Call SharedWork\nEnd Sub\n",
        )
    write_source(
        source_root, "Module_1.txt",
        "Public Sub SharedWork()\n    result = 1\nEnd Sub\n",
    )
    return source_root, tmp_path / "published"


def _stage(project: tuple[Path, Path]) -> Path:
    return project[1] / ".resumable"


def _completed_forms(project: tuple[Path, Path]) -> list[str]:
    data = json.loads((_stage(project) / "manifest.json").read_text(encoding="utf-8"))
    return [record["form_id"] for record in data["records"]]


def _document(project: tuple[Path, Path], number: int) -> Path:
    return project[1] / "docs" / f"Form_{number}.md"


def _fail_analysis_on_third(monkeypatch: pytest.MonkeyPatch) -> None:
    original = resumable.affected_entries_for_form

    def analyze(source_root, changes, form_id):
        if form_id == "Form_3.txt":
            raise RuntimeError("injected analysis failure")
        return original(source_root, changes, form_id)

    monkeypatch.setattr(resumable, "affected_entries_for_form", analyze)


@pytest.mark.parametrize("failure_phase", ["analysis", "llm", "staging", "manifest"])
def test_generation_failure_preserves_formal_outputs_and_resumes(
    project: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    baseline_revision = FileTracker(str(project[0])).scan().baseline_revision
    original_analysis = resumable.affected_entries_for_form
    original_write = resumable.atomic_write_text
    llm = DemoLlmClient()

    if failure_phase == "analysis":
        _fail_analysis_on_third(monkeypatch)
    elif failure_phase == "llm":
        calls = 0

        def generate(system_prompt, user_prompt):
            nonlocal calls
            calls += 1
            if calls == 5:
                raise RuntimeError("injected LLM failure")
            return DemoLlmClient().generate(system_prompt, user_prompt)

        llm = Mock(generate=Mock(side_effect=generate))
    else:
        def write(path, text):
            if (
                failure_phase == "staging"
                and path == resumable._payload_path(_stage(project), "Form_3.txt")
            ) or (
                failure_phase == "manifest" and path.name == "manifest.json"
                and len(json.loads(text)["records"]) == 3
            ):
                raise OSError("injected staging failure")
            original_write(path, text)

        monkeypatch.setattr(resumable, "atomic_write_text", write)

    with pytest.raises((RuntimeError, OSError), match="injected"):
        resumable.run_documentation(*project, llm)

    assert _completed_forms(project) == ["Form_1.txt", "Form_2.txt"]
    assert not (project[1] / "docs").exists()
    assert FileTracker(str(project[0])).scan().baseline_revision == baseline_revision
    saved = {
        number: resumable._payload_path(
            _stage(project), f"Form_{number}.txt",
        ).read_bytes()
        for number in (1, 2)
    }
    write_source(
        project[0], "Form_3.txt",
        "CodeBehindForm\nPrivate Sub Save_Click()\n"
        "    Call SharedWork\n    result = 3\nEnd Sub\n",
    )
    analysis = Mock(wraps=original_analysis)
    monkeypatch.setattr(resumable, "affected_entries_for_form", analysis)
    monkeypatch.setattr(resumable, "atomic_write_text", original_write)
    assert resumable.run_documentation(*project, DemoLlmClient()) == (
        "docs/Form_1.md", "docs/Form_2.md", "docs/Form_3.md",
    )
    assert [call.args[2] for call in analysis.call_args_list] == ["Form_3.txt"]
    for number, content in saved.items():
        assert _document(project, number).read_bytes() == content
    assert not _stage(project).exists()
    assert not FileTracker(str(project[0])).scan().has_changes
    assert "Skipped completed Form: Form_1.txt" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("changed_input", "expected_forms"),
    [
        ("form", ["Form_1.txt", "Form_3.txt"]),
        ("module", ["Form_1.txt", "Form_2.txt", "Form_3.txt"]),
        ("prompts", ["Form_1.txt", "Form_2.txt", "Form_3.txt"]),
        ("configuration", ["Form_1.txt", "Form_2.txt", "Form_3.txt"]),
        ("output", ["Form_1.txt", "Form_3.txt"]),
        ("staged_missing", ["Form_1.txt", "Form_3.txt"]),
        ("staged_corrupt", ["Form_1.txt", "Form_3.txt"]),
    ],
)
def test_staged_result_invalidation(
    project: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    changed_input: str,
    expected_forms: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_analysis = resumable.affected_entries_for_form
    _fail_analysis_on_third(monkeypatch)
    with pytest.raises(RuntimeError, match="injected"):
        resumable.run_documentation(*project, DemoLlmClient())
    version = "demo-v1"
    if changed_input == "form":
        path = project[0] / "Form_1.txt"
        path.write_text(path.read_text() + "' repaired\n")
    elif changed_input == "module":
        write_source(
            project[0], "Module_1.txt",
            "Public Sub SharedWork()\n    result = 2\nEnd Sub\n",
        )
    elif changed_input == "prompts":
        inputs = resumable._shared_inputs(project[0])
        monkeypatch.setattr(
            resumable, "_shared_inputs",
            lambda root: inputs + (("prompt-version", "changed"),),
        )
    elif changed_input == "configuration":
        version = "new-model"
    elif changed_input == "output":
        path = _document(project, 1)
        path.parent.mkdir(parents=True)
        path.write_text("Externally authored content\n", encoding="utf-8")
    else:
        path = resumable._payload_path(_stage(project), "Form_1.txt")
        if changed_input == "staged_missing":
            path.unlink()
        else:
            path.write_text("corrupted", encoding="utf-8")

    analysis = Mock(wraps=original_analysis)
    monkeypatch.setattr(resumable, "affected_entries_for_form", analysis)
    resumable.run_documentation(*project, DemoLlmClient(), generation_version=version)
    assert [call.args[2] for call in analysis.call_args_list] == expected_forms
    assert "Invalidated staged Form: Form_1.txt" in capsys.readouterr().out
    if changed_input == "output":
        assert "Externally authored content" in _document(project, 1).read_text()


def test_existing_document_and_prompt_history_are_preserved(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    resumable.run_documentation(*project, DemoLlmClient())
    path = _document(project, 1)
    previous = path.read_text(encoding="utf-8")
    extra = DocumentResult("unaffected", ResultKind.UPSERT, "Unchanged fragment")
    path.write_text("Handwritten introduction\n" + apply_result(previous, extra))
    write_source(
        project[0], "Module_1.txt",
        "Public Sub SharedWork()\n    result = 2\nEnd Sub\n",
    )
    llm = Mock(wraps=DemoLlmClient())
    original_analysis = resumable.affected_entries_for_form
    _fail_analysis_on_third(monkeypatch)
    saved = path.read_bytes()
    with pytest.raises(RuntimeError, match="injected"):
        resumable.run_documentation(*project, llm)
    assert path.read_bytes() == saved
    staged = resumable._payload_path(_stage(project), "Form_1.txt").read_text()
    assert "Handwritten introduction" in staged
    assert "Unchanged fragment" in staged
    assert any("<ExistingFragment>" in call.args[1] for call in llm.generate.call_args_list)
    monkeypatch.setattr(resumable, "affected_entries_for_form", original_analysis)
    resumable.run_documentation(*project, DemoLlmClient())
    assert path.read_text() == staged


def test_empty_form_is_certified_without_a_payload(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_source(project[0], "Form_1.txt", "Version =20\nBegin Form\nEnd\n")
    original_analysis = resumable.affected_entries_for_form
    _fail_analysis_on_third(monkeypatch)
    with pytest.raises(RuntimeError, match="injected"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert _completed_forms(project) == ["Form_1.txt", "Form_2.txt"]
    assert not resumable._payload_path(_stage(project), "Form_1.txt").exists()
    analysis = Mock(wraps=original_analysis)
    monkeypatch.setattr(resumable, "affected_entries_for_form", analysis)
    assert resumable.run_documentation(*project, DemoLlmClient()) == (
        "docs/Form_2.md", "docs/Form_3.md",
    )
    assert [call.args[2] for call in analysis.call_args_list] == ["Form_3.txt"]


@pytest.mark.parametrize("phase", ["generation", "preflight", "publication"])
def test_changing_inputs_cannot_advance_baseline(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    baseline = FileTracker(str(project[0])).scan().baseline_revision

    def change_source():
        write_source(
            project[0], "Module_1.txt",
            "Public Sub SharedWork()\n    result = 2\nEnd Sub\n",
        )

    llm = DemoLlmClient()
    if phase == "generation":
        def generate(system, user):
            change_source()
            return DemoLlmClient().generate(system, user)
        llm = Mock(generate=generate)
    elif phase == "preflight":
        original = resumable._save_batch
        def save(stage, batch):
            original(stage, batch)
            if batch.phase == "generating" and len(batch.records) == 3:
                change_source()
        monkeypatch.setattr(resumable, "_save_batch", save)
    else:
        original = resumable._publish_staged
        def publish(stage, root, record):
            changed = original(stage, root, record)
            change_source()
            return changed
        monkeypatch.setattr(resumable, "_publish_staged", publish)

    with pytest.raises(RevisionConflictError, match="changed"):
        resumable.run_documentation(*project, llm)
    assert FileTracker(str(project[0])).scan().baseline_revision == baseline
    if phase != "publication":
        assert not (project[1] / "docs").exists()
    if phase == "generation":
        assert _completed_forms(project) == []


def test_publication_failure_keeps_payloads_and_retries_without_generation(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = resumable._publish_staged
    def publish(stage, root, record):
        if record.form_id == "Form_3.txt":
            raise OSError("injected publication failure")
        return original(stage, root, record)
    monkeypatch.setattr(resumable, "_publish_staged", publish)
    with pytest.raises(OSError, match="publication failure"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert _completed_forms(project) == ["Form_1.txt", "Form_2.txt", "Form_3.txt"]
    assert _document(project, 1).exists()
    assert not _document(project, 3).exists()
    assert len(list(_stage(project).glob("*.md"))) == 3
    monkeypatch.setattr(resumable, "_publish_staged", original)
    analysis = Mock(side_effect=AssertionError("unexpected analysis"))
    monkeypatch.setattr(resumable, "affected_entries_for_form", analysis)
    llm = Mock(generate=Mock(side_effect=AssertionError("unexpected LLM")))
    assert resumable.run_documentation(*project, llm) == ("docs/Form_3.md",)
    analysis.assert_not_called()
    llm.generate.assert_not_called()
    assert not _stage(project).exists()


def test_atomic_formal_publication_preserves_original_on_replace_failure(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = _document(project, 1)
    path.parent.mkdir(parents=True)
    path.write_text("previous document", encoding="utf-8")
    original_replace = checkpoint.os.replace
    def replace(source, target):
        if target == path:
            raise OSError("injected replace failure")
        original_replace(source, target)
    monkeypatch.setattr(checkpoint.os, "replace", replace)
    with pytest.raises(OSError, match="replace failure"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert path.read_text() == "previous document"
    assert list(path.parent.iterdir()) == [path]
    assert resumable._payload_path(_stage(project), "Form_1.txt").exists()
    assert FileTracker(str(project[0])).scan().has_changes


def test_commit_failure_retries_without_generation_or_formal_writes(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_commit = FileTracker.commit
    monkeypatch.setattr(
        FileTracker, "commit",
        Mock(side_effect=RevisionConflictError("injected commit failure")),
    )
    with pytest.raises(RevisionConflictError, match="commit failure"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert _completed_forms(project) == ["Form_1.txt", "Form_2.txt", "Form_3.txt"]
    monkeypatch.setattr(FileTracker, "commit", original_commit)
    monkeypatch.setattr(
        resumable, "affected_entries_for_form",
        Mock(side_effect=AssertionError("unexpected analysis")),
    )
    original_write = resumable.atomic_write_text
    def write(path, text):
        assert path.parent != project[1] / "docs"
        original_write(path, text)
    monkeypatch.setattr(resumable, "atomic_write_text", write)
    assert resumable.run_documentation(*project, DemoLlmClient()) == ()
    assert not FileTracker(str(project[0])).scan().has_changes
    assert not _stage(project).exists()


@pytest.mark.parametrize("new_change", [False, True])
def test_cleanup_failure_after_commit_does_not_reuse_obsolete_batch(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, new_change: bool,
) -> None:
    original = resumable._clear_stage
    def clear(stage):
        resumable._payload_path(stage, "Form_1.txt").unlink(missing_ok=True)
        raise OSError("injected cleanup failure")
    monkeypatch.setattr(resumable, "_clear_stage", clear)
    with pytest.raises(OSError, match="cleanup failure"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert not FileTracker(str(project[0])).scan().has_changes
    monkeypatch.setattr(resumable, "_clear_stage", original)
    if new_change:
        path = project[0] / "Form_3.txt"
        path.write_text(path.read_text() + "' new change\n")
        analysis = Mock(wraps=resumable.affected_entries_for_form)
        monkeypatch.setattr(resumable, "affected_entries_for_form", analysis)
        resumable.run_documentation(*project, DemoLlmClient())
        assert [call.args[2] for call in analysis.call_args_list] == ["Form_3.txt"]
    else:
        monkeypatch.setattr(
            FileTracker, "commit", Mock(side_effect=AssertionError("repeated commit")),
        )
        monkeypatch.setattr(
            resumable, "affected_entries_for_form",
            Mock(side_effect=AssertionError("unexpected analysis")),
        )
        assert resumable.run_documentation(*project, DemoLlmClient()) == ()
    assert not _stage(project).exists()


@pytest.mark.parametrize("content", [
    "{", "[]", '{"version": 99}',
    '{"version": 1, "baseline_revision": "initial", "working_revision": "x",'
    ' "phase": "generating", "records": [{"form_id": "../Form_1.txt"}]}',
])
def test_invalid_manifest_is_not_silently_discarded(
    project: tuple[Path, Path], content: str,
) -> None:
    stage = _stage(project)
    stage.mkdir(parents=True)
    path = stage / "manifest.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        resumable.run_documentation(*project, DemoLlmClient())
    assert path.read_text() == content
    assert not (project[1] / "docs").exists()


def test_deleted_form_document_is_removed_only_during_publication(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    resumable.run_documentation(*project, DemoLlmClient())
    (project[0] / "Form_2.txt").unlink()
    original_publish = resumable._publish_staged
    monkeypatch.setattr(
        resumable, "_publish_staged",
        Mock(side_effect=OSError("injected publication failure")),
    )
    with pytest.raises(OSError, match="publication failure"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert _document(project, 2).exists()
    assert _completed_forms(project) == ["Form_2.txt"]
    assert not resumable._payload_path(_stage(project), "Form_2.txt").exists()
    monkeypatch.setattr(resumable, "_publish_staged", original_publish)
    monkeypatch.setattr(
        resumable, "affected_entries_for_form",
        Mock(side_effect=AssertionError("unexpected analysis")),
    )
    assert resumable.run_documentation(*project, DemoLlmClient()) == ("docs/Form_2.md",)
    assert not _document(project, 2).exists()
    assert not FileTracker(str(project[0])).scan().has_changes


def test_unchanged_run_does_not_commit_or_create_staging(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    resumable.run_documentation(*project, DemoLlmClient())
    monkeypatch.setattr(
        FileTracker, "commit", Mock(side_effect=AssertionError("unexpected commit")),
    )
    monkeypatch.setattr(
        resumable, "affected_entries_for_form",
        Mock(side_effect=AssertionError("unexpected analysis")),
    )
    assert resumable.run_documentation(*project, DemoLlmClient()) == ()
    assert not _stage(project).exists()


def test_formal_edit_after_staging_is_rejected_before_publication(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = resumable._save_batch
    def save(stage, batch):
        original(stage, batch)
        if batch.phase == "generating" and len(batch.records) == 3:
            path = _document(project, 3)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("external edit", encoding="utf-8")
    monkeypatch.setattr(resumable, "_save_batch", save)
    with pytest.raises(RevisionConflictError, match="document changed"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert not _document(project, 1).exists()
    assert _document(project, 3).read_text() == "external edit"


def test_staging_directory_symlink_is_rejected(
    project: tuple[Path, Path], tmp_path: Path,
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    project[1].mkdir()
    _stage(project).symlink_to(other, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic links"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert list(other.iterdir()) == []


def test_published_phase_write_failure_resumes_without_generation(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = resumable._save_batch
    def save(stage, batch):
        if batch.phase == "published":
            raise OSError("injected published manifest failure")
        original(stage, batch)
    monkeypatch.setattr(resumable, "_save_batch", save)
    with pytest.raises(OSError, match="published manifest failure"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert FileTracker(str(project[0])).scan().has_changes
    assert all(_document(project, number).exists() for number in (1, 2, 3))
    monkeypatch.setattr(resumable, "_save_batch", original)
    monkeypatch.setattr(
        resumable, "affected_entries_for_form",
        Mock(side_effect=AssertionError("unexpected analysis")),
    )
    assert resumable.run_documentation(*project, DemoLlmClient()) == ()
    assert not _stage(project).exists()


def test_abandoned_atomic_write_files_are_cleaned_without_touching_other_outputs(
    project: tuple[Path, Path],
) -> None:
    stage = _stage(project)
    stage.mkdir(parents=True)
    (stage / ".manifest.json.abcd1234.tmp").write_text("partial manifest")
    payload_name = resumable._payload_path(stage, "Form_1.txt").name
    (stage / f".{payload_name}.efgh5678.tmp").write_text("partial Markdown")
    other = project[1] / "unrelated.txt"
    other.write_text("keep")
    resumable.run_documentation(*project, DemoLlmClient())
    assert not stage.exists()
    assert other.read_text() == "keep"


def test_unknown_staging_file_is_not_deleted(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = resumable._clear_stage
    def clear(stage):
        (stage / "not-owned.txt").write_text("keep")
        original(stage)
    monkeypatch.setattr(resumable, "_clear_stage", clear)
    with pytest.raises(ValueError, match="unexpected staging file"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert (_stage(project) / "not-owned.txt").read_text() == "keep"
    assert (_stage(project) / "manifest.json").exists()
    assert len(list(_stage(project).glob("*.md"))) == 3


def test_llm_crlf_output_is_published_without_digest_mismatch(
    project: tuple[Path, Path],
) -> None:
    def generate(system, user):
        return DemoLlmClient().generate(system, user).replace("\n", "\r\n")
    assert resumable.run_documentation(*project, Mock(generate=generate)) == (
        "docs/Form_1.md", "docs/Form_2.md", "docs/Form_3.md",
    )
    assert b"\r\n" in _document(project, 1).read_bytes()


def test_reverted_form_staging_is_not_published(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    resumable.run_documentation(*project, DemoLlmClient())
    source = project[0] / "Form_1.txt"
    original_source = source.read_bytes()
    original_document = _document(project, 1).read_bytes()
    source.write_bytes(original_source + b"' changed\n")
    form3 = project[0] / "Form_3.txt"
    form3.write_bytes(form3.read_bytes() + b"' changed\n")
    original_analysis = resumable.affected_entries_for_form
    _fail_analysis_on_third(monkeypatch)
    with pytest.raises(RuntimeError, match="injected"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert _completed_forms(project) == ["Form_1.txt"]
    source.write_bytes(original_source)
    analysis = Mock(wraps=original_analysis)
    monkeypatch.setattr(resumable, "affected_entries_for_form", analysis)
    resumable.run_documentation(*project, DemoLlmClient())
    assert [call.args[2] for call in analysis.call_args_list] == ["Form_3.txt"]
    assert _document(project, 1).read_bytes() == original_document
    assert not _stage(project).exists()


def test_no_entry_changes_keep_document_without_llm_calls(
    project: tuple[Path, Path],
) -> None:
    resumable.run_documentation(*project, DemoLlmClient())
    path = project[0] / "Form_1.txt"
    path.write_text("Version =21\n" + path.read_text())
    original = _document(project, 1).read_bytes()
    llm = Mock(generate=Mock(side_effect=AssertionError("unexpected LLM")))
    assert resumable.run_documentation(*project, llm) == ()
    llm.generate.assert_not_called()
    assert _document(project, 1).read_bytes() == original
    assert not _stage(project).exists()


def test_external_edit_during_publication_is_not_overwritten(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = resumable._publish_staged
    def publish(stage, root, record):
        if record.form_id == "Form_2.txt":
            _document(project, 2).write_text("external edit")
        return original(stage, root, record)
    monkeypatch.setattr(resumable, "_publish_staged", publish)
    with pytest.raises(RevisionConflictError, match="document changed"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert _document(project, 2).read_text() == "external edit"
    assert not _document(project, 3).exists()
    assert FileTracker(str(project[0])).scan().has_changes


def test_output_edit_after_commit_prevents_silent_cleanup(
    project: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = resumable._clear_stage
    monkeypatch.setattr(
        resumable, "_clear_stage",
        Mock(side_effect=OSError("injected cleanup failure")),
    )
    with pytest.raises(OSError, match="cleanup failure"):
        resumable.run_documentation(*project, DemoLlmClient())
    _document(project, 1).write_text("external edit")
    monkeypatch.setattr(resumable, "_clear_stage", original)
    with pytest.raises(RevisionConflictError, match="published document changed"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert _stage(project).exists()
    assert _document(project, 1).read_text() == "external edit"
