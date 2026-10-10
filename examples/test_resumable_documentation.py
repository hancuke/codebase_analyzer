from pathlib import Path
import json
from unittest.mock import Mock

import pytest

import documentation_example_support as support
import progress_checkpoint as checkpoint
import resumable_documentation as resumable
from documentation_example_support import DemoLlmClient, DocumentPublisher, write_source
from filetracker import FileTracker, RevisionConflictError


@pytest.fixture
def project(tmp_path: Path) -> tuple[Path, Path, Path]:
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
    return source_root, tmp_path / "published", tmp_path / "progress.json"


def _completed_forms(progress_path: Path) -> list[str]:
    return sorted(json.loads(progress_path.read_text(encoding="utf-8")))


def _fail_analysis_on_third(monkeypatch: pytest.MonkeyPatch) -> Mock:
    original = resumable.affected_entries_for_form

    def analyze(source_root, changes, form_id):
        if form_id == "Form_3.txt":
            raise RuntimeError("injected analysis failure")
        return original(source_root, changes, form_id)

    analysis = Mock(side_effect=analyze)
    monkeypatch.setattr(resumable, "affected_entries_for_form", analysis)
    return analysis


@pytest.mark.parametrize("failure_phase", ["analysis", "llm", "publication"])
def test_restart_skips_successful_forms_after_failure_and_repair(
    project: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    failure_phase: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source_root, document_root, progress_path = project
    baseline_revision = FileTracker(str(source_root)).scan().baseline_revision
    original_analysis = resumable.affected_entries_for_form
    original_publish = DocumentPublisher.publish
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
        def publish(self, key, results):
            if key == "docs/Form_3.md":
                raise OSError("injected publication failure")
            return original_publish(self, key, results)

        monkeypatch.setattr(DocumentPublisher, "publish", publish)

    with pytest.raises((RuntimeError, OSError), match="injected"):
        resumable.run_documentation(*project, llm)

    assert _completed_forms(progress_path) == ["Form_1.txt", "Form_2.txt"]
    assert FileTracker(str(source_root)).scan().baseline_revision == baseline_revision
    saved_documents = {
        number: (document_root / "docs" / f"Form_{number}.md").read_bytes()
        for number in (1, 2)
    }
    assert not (document_root / "docs" / "Form_3.md").exists()

    # Repair only the failed Form: the global working revision changes.
    write_source(
        source_root, "Form_3.txt",
        "CodeBehindForm\nPrivate Sub Save_Click()\n"
        "    Call SharedWork\n    result = 3\nEnd Sub\n",
    )
    analysis = Mock(wraps=original_analysis)
    monkeypatch.setattr(resumable, "affected_entries_for_form", analysis)
    monkeypatch.setattr(DocumentPublisher, "publish", original_publish)
    updated = resumable.run_documentation(*project, DemoLlmClient())

    assert updated == ("docs/Form_3.md",)
    assert [call.args[2] for call in analysis.call_args_list] == ["Form_3.txt"]
    for number, content in saved_documents.items():
        assert (document_root / "docs" / f"Form_{number}.md").read_bytes() == content
    assert _completed_forms(progress_path) == []
    assert not FileTracker(str(source_root)).scan().has_changes
    assert "Skipped completed Form: Form_1.txt" in capsys.readouterr().out
    analysis.reset_mock()
    assert resumable.run_documentation(*project, DemoLlmClient()) == ()
    analysis.assert_not_called()


@pytest.mark.parametrize(
    ("changed_input", "expected_forms"),
    [
        ("form", ["Form_1.txt", "Form_3.txt"]),
        ("module", ["Form_1.txt", "Form_2.txt", "Form_3.txt"]),
        ("prompts", ["Form_1.txt", "Form_2.txt", "Form_3.txt"]),
        ("configuration", ["Form_1.txt", "Form_2.txt", "Form_3.txt"]),
        ("output", ["Form_1.txt", "Form_3.txt"]),
    ],
)
def test_checkpoint_invalidation(
    project: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    changed_input: str,
    expected_forms: list[str],
) -> None:
    source_root, document_root, _ = project
    original_analysis = resumable.affected_entries_for_form
    _fail_analysis_on_third(monkeypatch)
    with pytest.raises(RuntimeError, match="injected"):
        resumable.run_documentation(*project, DemoLlmClient())

    version = "demo-v1"
    if changed_input == "form":
        path = source_root / "Form_1.txt"
        path.write_text(path.read_text() + "' repaired\n")
    elif changed_input == "module":
        write_source(
            source_root, "Module_1.txt",
            "Public Sub SharedWork()\n    result = 2\nEnd Sub\n",
        )
    elif changed_input == "prompts":
        original_inputs = resumable._shared_inputs(source_root)
        monkeypatch.setattr(
            resumable, "_shared_inputs",
            lambda root: original_inputs + (("prompt-version", "changed"),),
        )
    elif changed_input == "configuration":
        version = "new-model"
    else:
        (document_root / "docs" / "Form_1.md").unlink()

    analysis = Mock(wraps=original_analysis)
    monkeypatch.setattr(resumable, "affected_entries_for_form", analysis)
    resumable.run_documentation(
        *project, DemoLlmClient(), generation_version=version,
    )
    assert [call.args[2] for call in analysis.call_args_list] == expected_forms


def test_empty_form_is_checkpointed(
    project: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, _, progress_path = project
    write_source(source_root, "Form_1.txt", "Version =20\nBegin Form\nEnd\n")
    _fail_analysis_on_third(monkeypatch)

    with pytest.raises(RuntimeError, match="injected"):
        resumable.run_documentation(*project, DemoLlmClient())

    assert _completed_forms(progress_path) == ["Form_1.txt", "Form_2.txt"]


def test_changed_inputs_during_generation_are_not_checkpointed(
    project: tuple[Path, Path, Path],
) -> None:
    source_root, _, progress_path = project

    def generate(system_prompt, user_prompt):
        write_source(
            source_root, "Module_1.txt",
            "Public Sub SharedWork()\n    result = 2\nEnd Sub\n",
        )
        return DemoLlmClient().generate(system_prompt, user_prompt)

    with pytest.raises(RevisionConflictError, match="inputs changed"):
        resumable.run_documentation(*project, Mock(generate=generate))

    assert not progress_path.exists()
    assert FileTracker(str(source_root)).scan().has_changes


def test_atomic_publication_preserves_existing_file_on_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "document.md"
    path.write_text("previous document", encoding="utf-8")
    monkeypatch.setattr(
        checkpoint.os, "replace", Mock(side_effect=OSError("injected replace failure")),
    )

    with pytest.raises(OSError, match="injected"):
        support.atomic_write_text(path, "new document")

    assert path.read_text(encoding="utf-8") == "previous document"
    assert list(tmp_path.iterdir()) == [path]


def test_checkpoint_write_failure_does_not_mark_form_complete(
    project: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, document_root, progress_path = project
    original_analysis = resumable.affected_entries_for_form
    original_save = checkpoint.ProgressCheckpoint.record_success
    monkeypatch.setattr(
        checkpoint.ProgressCheckpoint, "record_success",
        Mock(side_effect=OSError("injected checkpoint failure")),
    )
    with pytest.raises(OSError, match="checkpoint failure"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert not progress_path.exists()
    assert (document_root / "docs" / "Form_1.md").exists()

    monkeypatch.setattr(checkpoint.ProgressCheckpoint, "record_success", original_save)
    analysis = Mock(wraps=original_analysis)
    monkeypatch.setattr(resumable, "affected_entries_for_form", analysis)
    resumable.run_documentation(*project, DemoLlmClient())
    assert [call.args[2] for call in analysis.call_args_list] == [
        "Form_1.txt", "Form_2.txt", "Form_3.txt",
    ]


@pytest.mark.parametrize("content", ['{', '[]', '{"Form_1.txt": [1, 2]}'])
def test_invalid_checkpoint_is_not_silently_discarded(
    project: tuple[Path, Path, Path], content: str,
) -> None:
    _, document_root, progress_path = project
    progress_path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError):
        resumable.run_documentation(*project, DemoLlmClient())

    assert not document_root.exists()
    assert progress_path.read_text(encoding="utf-8") == content


def test_commit_failure_keeps_all_completed_forms_for_retry(
    project: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root, _, progress_path = project
    original_commit = FileTracker.commit
    monkeypatch.setattr(
        FileTracker, "commit",
        Mock(side_effect=RevisionConflictError("injected commit failure")),
    )
    with pytest.raises(RevisionConflictError, match="commit failure"):
        resumable.run_documentation(*project, DemoLlmClient())
    assert _completed_forms(progress_path) == [
        "Form_1.txt", "Form_2.txt", "Form_3.txt",
    ]
    assert FileTracker(str(source_root)).scan().has_changes

    monkeypatch.setattr(FileTracker, "commit", original_commit)
    analysis = Mock(side_effect=AssertionError("completed Form was reanalyzed"))
    monkeypatch.setattr(resumable, "affected_entries_for_form", analysis)
    assert resumable.run_documentation(*project, DemoLlmClient()) == ()
    analysis.assert_not_called()
    assert not FileTracker(str(source_root)).scan().has_changes
    assert _completed_forms(progress_path) == []


def test_deleted_form_document_is_removed(
    project: tuple[Path, Path, Path],
) -> None:
    source_root, document_root, _ = project
    resumable.run_documentation(*project, DemoLlmClient())
    (source_root / "Form_2.txt").unlink()

    assert resumable.run_documentation(*project, DemoLlmClient()) == ("docs/Form_2.md",)
    assert not (document_root / "docs" / "Form_2.md").exists()
    assert not FileTracker(str(source_root)).scan().has_changes
