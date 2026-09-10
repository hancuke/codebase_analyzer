"""Python symbol parser backed by the standard-library :mod:`ast` module."""

from __future__ import annotations

import ast

from symbol_tracker.base_parser import SymbolParser
from symbol_tracker.models import SymbolState, SymbolType


class PythonASTParser(SymbolParser):
    def parse(self, code_text: str) -> list[SymbolState]:
        if not code_text or not code_text.strip():
            return []

        try:
            tree = ast.parse(code_text)
        except SyntaxError:
            # Invalid Python source: fail safe, never raise.
            return []

        lines = code_text.splitlines()
        symbols: list[SymbolState] = []
        # Walk every definition (module-level, nested in classes, and nested in
        # other functions) so no symbol is silently dropped. Names are fully
        # qualified (e.g. ``UserService.login`` or ``outer.inner``) and a symbol
        # is only a METHOD when its direct enclosing scope is a class.
        self._walk(tree, lines, prefix="", in_class=False, symbols=symbols)
        return symbols

    def _walk(
        self,
        node: ast.AST,
        lines: list[str],
        prefix: str,
        in_class: bool,
        symbols: list[SymbolState],
    ) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._emit_function(child, lines, prefix, in_class, symbols)
            elif isinstance(child, ast.ClassDef):
                self._emit_class(child, lines, prefix, symbols)

    def _emit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        lines: list[str],
        prefix: str,
        in_class: bool,
        symbols: list[SymbolState],
    ) -> None:
        decorator_lines = [decorator.lineno for decorator in node.decorator_list]
        start = min(decorator_lines, default=node.lineno) - 1
        end_line_idx = getattr(node, "end_lineno", start + 1)
        func_code = "\n".join(lines[start:end_line_idx])

        name = f"{prefix}{node.name}"
        declaration_end = node.body[0].lineno - 1 if node.body else node.lineno
        declaration = "\n".join(lines[start:declaration_end]).rstrip()

        symbols.append(
            SymbolState(
                name=name,
                symbol_type=SymbolType.METHOD if in_class else SymbolType.FUNCTION,
                declaration=declaration,
                content=func_code,
                body_hash=self.compute_hash(func_code),
                start_line=node.lineno,
                end_line=end_line_idx,
            )
        )
        # Recurse into the function body for nested functions (these are plain
        # functions, not methods, regardless of nesting depth).
        self._walk(node, lines, prefix=f"{name}.", in_class=False, symbols=symbols)

    def _emit_class(
        self,
        node: ast.ClassDef,
        lines: list[str],
        prefix: str,
        symbols: list[SymbolState],
    ) -> None:
        decorator_lines = [decorator.lineno for decorator in node.decorator_list]
        start = min(decorator_lines, default=node.lineno) - 1
        end_line_idx = getattr(node, "end_lineno", start + 1)
        class_code = "\n".join(lines[start:end_line_idx])

        declaration_end = node.body[0].lineno - 1 if node.body else node.lineno
        declaration = "\n".join(lines[start:declaration_end]).rstrip()

        symbols.append(
            SymbolState(
                name=f"{prefix}{node.name}",
                symbol_type=SymbolType.CLASS,
                declaration=declaration,
                content=class_code,
                body_hash=self.compute_hash(class_code),
                start_line=node.lineno,
                end_line=end_line_idx,
            )
        )
        # Recurse into the class body; members are methods.
        self._walk(node, lines, prefix=f"{prefix}{node.name}.", in_class=True, symbols=symbols)
