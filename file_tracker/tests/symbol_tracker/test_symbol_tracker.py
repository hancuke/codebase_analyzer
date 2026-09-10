"""Tests for per-file SymbolTracker extraction."""

from __future__ import annotations

from pathlib import Path

from filetracker import ChangeStatus, ContentAvailability
from filetracker.tracker import FileTracker
from symbol_tracker import SymbolExtractionOptions
from symbol_tracker.parsers.python_parser import PythonASTParser
from symbol_tracker.registry import ParserRegistry
from symbol_tracker.tracker import SymbolTracker


def _make_trackers(tmp_path):
    (tmp_path / "module.py").write_text("def existing():\n    return 1\n")
    file_tracker = FileTracker(root=str(tmp_path))
    file_tracker.commit()
    parser_registry = ParserRegistry()
    parser_registry.register(".py", PythonASTParser())
    return file_tracker, SymbolTracker(parser_registry)


def _extract_only_change(file_tracker, symbol_tracker, options=None):
    changes = file_tracker.scan()
    assert len(changes.files) == 1
    return symbol_tracker.extract_symbol_changes(changes.files[0], options)


def test_renamed_function_is_scoped_to_its_file(tmp_path):
    file_tracker, symbol_tracker = _make_trackers(tmp_path)
    (tmp_path / "module.py").write_text("def renamed():\n    return 1\n")

    result = _extract_only_change(file_tracker, symbol_tracker)

    assert result.file_change.path == Path("module.py")
    assert {change.symbol_name for change in result.added} == {"renamed"}
    assert {change.symbol_name for change in result.deleted} == {"existing"}


def test_modified_function_reports_a_symbol_diff(tmp_path):
    file_tracker, symbol_tracker = _make_trackers(tmp_path)
    (tmp_path / "module.py").write_text("def existing():\n    return 2\n")

    result = _extract_only_change(file_tracker, symbol_tracker)

    assert [change.symbol_name for change in result.modified] == ["existing"]
    assert "+    return 2" in result.modified[0].diff()


def test_new_and_deleted_files_use_only_available_content(tmp_path):
    file_tracker = FileTracker(root=str(tmp_path))
    file_tracker.commit()
    (tmp_path / "created.py").write_text("def created():\n    return 1\n")
    parser_registry = ParserRegistry()
    parser_registry.register(".py", PythonASTParser())
    symbol_tracker = SymbolTracker(parser_registry)

    added_result = _extract_only_change(file_tracker, symbol_tracker)
    assert added_result.file_change.baseline_content.availability is ContentAvailability.ABSENT
    assert [change.symbol_name for change in added_result.added] == ["created"]

    file_tracker.commit()
    (tmp_path / "created.py").unlink()
    deleted_result = _extract_only_change(file_tracker, symbol_tracker)
    assert deleted_result.file_change.working_content.availability is ContentAvailability.ABSENT
    assert [change.symbol_name for change in deleted_result.deleted] == ["created"]


def test_unregistered_files_are_preserved_without_symbol_changes(tmp_path):
    (tmp_path / "notes.txt").write_text("old")
    file_tracker = FileTracker(root=str(tmp_path))
    file_tracker.commit()
    (tmp_path / "notes.txt").write_text("new")

    result = _extract_only_change(file_tracker, SymbolTracker())

    assert result.file_change.path == Path("notes.txt")
    assert result.symbol_changes == ()


def test_default_extraction_avoids_duplicate_class_and_nested_changes(tmp_path):
    file_tracker = FileTracker(root=str(tmp_path))
    (tmp_path / "module.py").write_text(
        "class Service:\n"
        "    def run(self):\n"
        "        return 1\n"
        "\n"
        "def outer():\n"
        "    def inner():\n"
        "        return 1\n"
        "    return inner()\n"
    )
    file_tracker.commit()
    (tmp_path / "module.py").write_text(
        "class Service:\n"
        "    def run(self):\n"
        "        return 2\n"
        "\n"
        "def outer():\n"
        "    def inner():\n"
        "        return 2\n"
        "    return inner()\n"
    )
    parser_registry = ParserRegistry()
    parser_registry.register(".py", PythonASTParser())
    symbol_tracker = SymbolTracker(parser_registry)

    default_result = _extract_only_change(file_tracker, symbol_tracker)
    detailed_result = _extract_only_change(
        file_tracker,
        symbol_tracker,
        SymbolExtractionOptions(include_classes=True, include_nested_functions=True),
    )

    assert [change.symbol_name for change in default_result.modified] == [
        "Service.run",
        "outer",
    ]
    assert {change.symbol_name for change in detailed_result.modified} == {
        "Service",
        "Service.run",
        "outer",
        "outer.inner",
    }
