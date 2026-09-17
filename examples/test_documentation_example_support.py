from pathlib import Path

from documentation_example_support import (
    _read_vba_source,
    affected_entries_for_form,
    form_source_ids_to_analyze,
    write_source,
)
from filetracker import FileTracker


def _write_form(root: Path, caption: str = "Orders") -> None:
    write_source(
        root,
        "Form_1.txt",
        "Version =20\n"
        "Begin Form\n"
        f'    Caption ="{caption}"\n'
        "End\n"
        "CodeBehindForm\n"
        "Private Sub Save_Click()\n"
        "    Call SharedWork\n"
        "End Sub\n",
        encoding="utf-16",
    )


def test_form_source_contains_only_code_behind(tmp_path: Path) -> None:
    _write_form(tmp_path)

    source = _read_vba_source(tmp_path / "Form_1.txt")

    assert source == (
        "Private Sub Save_Click()\n"
        "    Call SharedWork\n"
        "End Sub\n"
    )


def test_form_without_code_behind_has_empty_vba_source(tmp_path: Path) -> None:
    write_source(
        tmp_path,
        "Form_1.txt",
        "Version =20\nBegin Form\nEnd\n",
        encoding="utf-16",
    )

    assert _read_vba_source(tmp_path / "Form_1.txt") == ""


def test_utf16_module_is_supported(tmp_path: Path) -> None:
    write_source(
        tmp_path,
        "Module_1.txt",
        "Public Sub SharedWork()\nEnd Sub\n",
        encoding="utf-16",
    )

    assert _read_vba_source(tmp_path / "Module_1.txt") == (
        "Public Sub SharedWork()\nEnd Sub\n"
    )


def test_form_ui_only_change_does_not_affect_entries(tmp_path: Path) -> None:
    _write_form(tmp_path)
    write_source(
        tmp_path,
        "Module_1.txt",
        "Public Sub SharedWork()\nEnd Sub\n",
    )
    tracker = FileTracker(str(tmp_path))
    tracker.commit(message="baseline")

    _write_form(tmp_path, caption="Updated orders")
    change_set = tracker.scan()

    assert form_source_ids_to_analyze(tmp_path, change_set) == ("Form_1.txt",)
    assert affected_entries_for_form(
        tmp_path,
        change_set,
        "Form_1.txt",
    ) == ()


def test_form_with_utf8_or_ansi_encoding_does_not_fail(tmp_path: Path) -> None:
    write_source(
        tmp_path,
        "Form_1.txt",
        "Version =20\nBegin Form\nEnd\nCodeBehindForm\nPrivate Sub Save_Click()\nEnd Sub\n",
        encoding="utf-8",
    )
    assert _read_vba_source(tmp_path / "Form_1.txt") == "Private Sub Save_Click()\nEnd Sub\n"

    write_source(
        tmp_path,
        "Form_2.txt",
        "Version =20\nBegin Form\nEnd\nCodeBehindForm\nPrivate Sub Save_Click()\nEnd Sub\n",
        encoding="gbk",
    )
    assert _read_vba_source(tmp_path / "Form_2.txt") == "Private Sub Save_Click()\nEnd Sub\n"
