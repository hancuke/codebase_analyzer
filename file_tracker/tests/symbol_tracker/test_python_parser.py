"""Tests for the Python AST symbol parser."""

from __future__ import annotations

from symbol_tracker.models import SymbolType
from symbol_tracker.parsers.python_parser import PythonASTParser


def test_python_syntax_error_returns_no_symbols():
    parser = PythonASTParser()
    assert parser.parse("def foo(:\n") == []
    assert parser.parse("") == []


def test_function_declaration_preserves_annotations_defaults_and_decorators():
    code = (
        "@decorator(flag=True)\n"
        "async def fetch(value: int = 1, *, timeout: float = 2.0) -> str:\n"
        "    return str(value)\n"
    )

    symbol = PythonASTParser().parse(code)[0]

    assert symbol.name == "fetch"
    assert symbol.symbol_type is SymbolType.FUNCTION
    assert symbol.declaration == (
        "@decorator(flag=True)\n"
        "async def fetch(value: int = 1, *, timeout: float = 2.0) -> str:"
    )
    assert symbol.content.startswith("@decorator")


def test_parser_qualifies_classes_methods_and_nested_functions():
    code = (
        "class UserService:\n"
        "    def login(self):\n"
        "        return True\n"
        "\n"
        "def outer():\n"
        "    def inner():\n"
        "        return 1\n"
        "    return inner()\n"
    )

    symbols = {symbol.name: symbol for symbol in PythonASTParser().parse(code)}

    assert symbols["UserService"].symbol_type is SymbolType.CLASS
    assert symbols["UserService.login"].symbol_type is SymbolType.METHOD
    assert symbols["outer.inner"].symbol_type is SymbolType.FUNCTION


def test_hash_changes_when_declaration_or_body_changes():
    base = "def foo() -> int:\n    return 1\n"
    changed = "def foo() -> str:\n    return '1'\n"

    old_symbol = PythonASTParser().parse(base)[0]
    new_symbol = PythonASTParser().parse(changed)[0]

    assert old_symbol.declaration != new_symbol.declaration
    assert old_symbol.body_hash != new_symbol.body_hash
