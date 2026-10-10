from pathlib import Path
from unittest.mock import Mock, call

import pytest

from documentation_example_support import (
    _load_prompt_templates,
    _read_vba_source,
    affected_entries_for_form,
    affected_entries_by_form,
    DemoLlmClient,
    DocumentPublisher,
    PromptTemplates,
    form_source_ids_to_analyze,
    generate_entry_document,
    publish_documents_for_forms,
    write_source,
)
from initial_oracle_documentation import (
    _function_changes_for_scan,
    _publish_documents,
    reachable_dependency_paths_and_functions,
)
from change_analyzer import ChangeType, EntryChange
from codegraph import Codebase, OraclePlsqlAnalyzer, SourceFile
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


def test_affected_entries_by_form_includes_each_form_for_shared_module_change(
    tmp_path: Path,
) -> None:
    _write_form(tmp_path)
    write_source(
        tmp_path,
        "Form_2.txt",
        "CodeBehindForm\n"
        "Private Sub Cancel_Click()\n"
        "    Call SharedWork\n"
        "End Sub\n",
    )
    write_source(
        tmp_path,
        "Module_1.txt",
        "Public Sub SharedWork()\n    result = 1\nEnd Sub\n",
    )
    tracker = FileTracker(str(tmp_path))
    tracker.commit(message="baseline")
    write_source(
        tmp_path,
        "Module_1.txt",
        "Public Sub SharedWork()\n    result = 2\nEnd Sub\n",
    )

    changes_by_form = affected_entries_by_form(tmp_path, tracker.scan())

    assert list(changes_by_form) == ["Form_1.txt", "Form_2.txt"]
    assert all(changes_by_form.values())


def test_publish_documents_for_forms_returns_changed_document_keys(
    tmp_path: Path,
) -> None:
    _write_form(tmp_path)
    write_source(
        tmp_path,
        "Module_1.txt",
        "Public Sub SharedWork()\n    result = 1\nEnd Sub\n",
    )
    tracker = FileTracker(str(tmp_path))
    changes_by_form = affected_entries_by_form(tmp_path, tracker.scan())
    publisher = DocumentPublisher(tmp_path / "published")

    updated_document_keys = publish_documents_for_forms(
        changes_by_form,
        publisher,
        DemoLlmClient(),
    )

    assert updated_document_keys == ("docs/Form_1.md",)
    assert (tmp_path / "published" / "docs" / "Form_1.md").exists()


def _entry_change(tmp_path: Path) -> EntryChange:
    _write_form(tmp_path)
    tracker = FileTracker(str(tmp_path))
    return affected_entries_by_form(tmp_path, tracker.scan())["Form_1.txt"][0]


def _prompt_directories(tmp_path: Path) -> tuple[Path, Path]:
    system_directory = tmp_path / "system"
    user_directory = tmp_path / "user"
    system_directory.mkdir()
    user_directory.mkdir()
    (system_directory / "instructions.txt").write_text("Shared", encoding="utf-8")
    return system_directory, user_directory


def test_load_prompt_templates_returns_unformatted_templates(tmp_path: Path) -> None:
    system_directory, user_directory = _prompt_directories(tmp_path)
    (user_directory / "01-first.user.template").write_text(
        "{entry_id}: {code}{history}", encoding="utf-8"
    )

    assert _load_prompt_templates(system_directory, user_directory) == PromptTemplates(
        "Shared",
        ("{entry_id}: {code}{history}",),
    )


def test_generate_entry_document_discovers_numbered_user_prompts_in_order(
    tmp_path: Path,
) -> None:
    change = _entry_change(tmp_path)
    system_directory, user_directory = _prompt_directories(tmp_path)
    (user_directory / "03-extra.user.template").write_text(
        "Third {entry_id}", encoding="utf-8"
    )
    (user_directory / "02-boundaries.user.template").write_text(
        "Second {entry_id}", encoding="utf-8"
    )
    (user_directory / "01-business_flow.user.template").write_text(
        "First {entry_id}", encoding="utf-8"
    )
    (user_directory / "ignored.txt").write_text("Ignored", encoding="utf-8")
    llm = Mock()
    llm.generate.side_effect = ["first", "second", "third"]

    result = generate_entry_document(
        change,
        fragment_id="entry:test",
        existing_markdown=None,
        llm=llm,
        system_directory=system_directory,
        user_directory=user_directory,
    )

    assert result.markdown == "first\n\nsecond\n\nthird"
    assert llm.generate.call_args_list == [
        call("Shared", f"First {change.entry_id}"),
        call("Shared", f"Second {change.entry_id}"),
        call("Shared", f"Third {change.entry_id}"),
    ]


@pytest.mark.parametrize(
    ("system_file_count", "user_file_count", "error"),
    [
        (0, 1, "expected exactly one system prompt file"),
        (2, 1, "expected exactly one system prompt file"),
        (1, 0, "no numbered user prompt templates"),
    ],
)
def test_generate_entry_document_rejects_invalid_prompt_counts(
    tmp_path: Path,
    system_file_count: int,
    user_file_count: int,
    error: str,
) -> None:
    change = _entry_change(tmp_path)
    system_directory, user_directory = _prompt_directories(tmp_path)
    if system_file_count == 0:
        (system_directory / "instructions.txt").unlink()
    elif system_file_count == 2:
        (system_directory / "another.txt").write_text("Another", encoding="utf-8")
    if user_file_count:
        (user_directory / "01-first.user.template").write_text("{entry_id}", encoding="utf-8")
    llm = Mock()

    with pytest.raises(ValueError, match=error):
        generate_entry_document(
            change,
            fragment_id="entry:test",
            existing_markdown=None,
            llm=llm,
            system_directory=system_directory,
            user_directory=user_directory,
        )
    llm.generate.assert_not_called()


def test_generate_entry_document_rejects_shared_prompt_directory(tmp_path: Path) -> None:
    change = _entry_change(tmp_path)
    system_directory, _ = _prompt_directories(tmp_path)
    llm = Mock()

    with pytest.raises(ValueError, match="directories must differ"):
        generate_entry_document(
            change,
            fragment_id="entry:test",
            existing_markdown=None,
            llm=llm,
            system_directory=system_directory,
            user_directory=system_directory,
        )
    llm.generate.assert_not_called()


def test_generate_entry_document_reads_all_prompts_before_calling_llm(
    tmp_path: Path,
) -> None:
    change = _entry_change(tmp_path)
    system_directory, user_directory = _prompt_directories(tmp_path)
    (user_directory / "01-first.user.template").write_text("{entry_id}", encoding="utf-8")
    (user_directory / "02-second.user.template").write_text("  ", encoding="utf-8")
    llm = Mock()

    with pytest.raises(ValueError, match="empty prompt file"):
        generate_entry_document(
            change,
            fragment_id="entry:test",
            existing_markdown=None,
            llm=llm,
            system_directory=system_directory,
            user_directory=user_directory,
        )
    llm.generate.assert_not_called()


def test_generate_entry_document_formats_all_templates_before_calling_llm(
    tmp_path: Path,
) -> None:
    change = _entry_change(tmp_path)
    system_directory, user_directory = _prompt_directories(tmp_path)
    (user_directory / "01-first.user.template").write_text(
        "{entry_id}", encoding="utf-8"
    )
    (user_directory / "02-second.user.template").write_text(
        "{unknown}", encoding="utf-8"
    )
    llm = Mock()

    with pytest.raises(KeyError, match="unknown"):
        generate_entry_document(
            change,
            fragment_id="entry:test",
            existing_markdown=None,
            llm=llm,
            system_directory=system_directory,
            user_directory=user_directory,
        )
    llm.generate.assert_not_called()


def test_oracle_second_scan_maps_intermediate_change_to_entry(
    tmp_path: Path,
) -> None:
    write_source(
        tmp_path,
        "orders.pks",
        "CREATE OR REPLACE PACKAGE orders AS\n"
        "    PROCEDURE save_order(p_id NUMBER);\n"
        "END orders;\n",
    )
    write_source(
        tmp_path,
        "orders.pkb",
        "CREATE OR REPLACE PACKAGE BODY orders AS\n"
        "    PROCEDURE save_order(p_id NUMBER) IS\n"
        "    BEGIN\n"
        "        audit_pkg.write_log(p_id);\n"
        "    END save_order;\n"
        "END orders;\n",
    )
    write_source(
        tmp_path,
        "audit.pkb",
        "CREATE OR REPLACE PACKAGE BODY audit_pkg AS\n"
        "    PROCEDURE write_log(p_id NUMBER) IS\n"
        "    BEGIN\n"
        "        NULL;\n"
        "    END write_log;\n"
        "END audit_pkg;\n",
    )
    tracker = FileTracker(str(tmp_path))
    tracker.commit(message="baseline")

    write_source(
        tmp_path,
        "audit.pkb",
        "CREATE OR REPLACE PACKAGE BODY audit_pkg AS\n"
        "    PROCEDURE write_log(p_id NUMBER) IS\n"
        "    BEGIN\n"
        "        INSERT INTO audit_log(id) VALUES (p_id);\n"
        "    END write_log;\n"
        "END audit_pkg;\n",
    )

    changes = _function_changes_for_scan(tmp_path, tracker.scan())

    assert [change.function_id for change in changes] == [
        "plsql:audit_pkg:write_log"
    ]
    assert [
        (item.function_id, item.change_type)
        for item in changes
    ] == [("plsql:audit_pkg:write_log", ChangeType.MODIFIED)]


def test_oracle_initial_scan_includes_functions_without_entry_points(
    tmp_path: Path,
) -> None:
    write_source(
        tmp_path,
        "orders.pkb",
        "CREATE OR REPLACE PACKAGE BODY orders AS\n"
        "    PROCEDURE save_order(p_id NUMBER) IS\n"
        "    BEGIN\n"
        "        audit_pkg.write_log(p_id);\n"
        "    END save_order;\n"
        "END orders;\n",
    )
    write_source(
        tmp_path,
        "audit.pkb",
        "CREATE OR REPLACE PACKAGE BODY audit_pkg AS\n"
        "    PROCEDURE write_log(p_id NUMBER) IS\n"
        "    BEGIN\n"
        "        NULL;\n"
        "    END write_log;\n"
        "END audit_pkg;\n",
    )
    tracker = FileTracker(str(tmp_path))

    changes = _function_changes_for_scan(tmp_path, tracker.scan())

    assert [change.function_id for change in changes] == [
        "plsql:audit_pkg:write_log",
        "plsql:orders:save_order",
    ]
    assert all(change.new_function is not None for change in changes)
    publisher = DocumentPublisher(tmp_path / "published")

    assert _publish_documents(changes, publisher, DemoLlmClient()) == (
        "docs/audit.md",
        "docs/orders.md",
    )
    assert publisher.read_fragment(
        "docs/audit.md",
        "function:plsql:audit_pkg:write_log",
    )
    assert publisher.read_fragment(
        "docs/orders.md",
        "function:plsql:orders:save_order",
    )


def test_reachable_dependency_context_returns_paths_and_function_code(
    tmp_path: Path,
) -> None:
    write_source(
        tmp_path,
        "orders.pkb",
        "CREATE OR REPLACE PACKAGE BODY orders AS\n"
        "    PROCEDURE save_order(p_id NUMBER) IS\n"
        "    BEGIN\n"
        "        audit_pkg.write_log(p_id);\n"
        "    END save_order;\n"
        "END orders;\n",
    )
    write_source(
        tmp_path,
        "audit.pkb",
        "CREATE OR REPLACE PACKAGE BODY audit_pkg AS\n"
        "    PROCEDURE write_log(p_id NUMBER) IS\n"
        "    BEGIN\n"
        "        sink_pkg.write_line(p_id);\n"
        "    END write_log;\n"
        "END audit_pkg;\n",
    )
    write_source(
        tmp_path,
        "sink.pkb",
        "CREATE OR REPLACE PACKAGE BODY sink_pkg AS\n"
        "    PROCEDURE write_line(p_id NUMBER) IS\n"
        "    BEGIN\n"
        "        NULL;\n"
        "    END write_line;\n"
        "END sink_pkg;\n",
    )
    codebase = Codebase.analyze(
        tuple(
            SourceFile(
                source_id=path.name,
                content=path.read_text(encoding="utf-8"),
                language="plsql",
            )
            for path in sorted(tmp_path.glob("*.pkb"))
        ),
        [OraclePlsqlAnalyzer()],
    )

    paths, dependencies = reachable_dependency_paths_and_functions(
        codebase,
        "plsql:orders:save_order",
    )

    assert paths == (
        ("plsql:orders:save_order", "plsql:audit_pkg:write_log"),
        (
            "plsql:orders:save_order",
            "plsql:audit_pkg:write_log",
            "plsql:sink_pkg:write_line",
        ),
    )
    assert [
        (function.id, function.source_id)
        for function in dependencies
    ] == [
        ("plsql:audit_pkg:write_log", "audit.pkb"),
        ("plsql:sink_pkg:write_line", "sink.pkb"),
    ]
    assert "sink_pkg.write_line(p_id);" in dependencies[0].source
    assert "NULL;" in dependencies[1].source
