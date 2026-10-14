"""Caller-side analysis and document reading for the workbench example."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from change_analyzer import FunctionChange, analyze_changes
from codegraph import OraclePlsqlAnalyzer, SourceFile
from filetracker import ChangeSet


def analyze_documents(
    source_root: Path, changes: ChangeSet
) -> Mapping[str, tuple[FunctionChange, ...]]:
    sources = tuple(
        SourceFile(
            source_id=path.relative_to(source_root).as_posix(),
            content=path.read_text(encoding="utf-8"),
            language="plsql",
        )
        for path in sorted(source_root.rglob("*.pkb"))
        if path.is_file()
    )
    report = analyze_changes(changes, sources, [OraclePlsqlAnalyzer()])
    grouped: dict[str, list[FunctionChange]] = {}
    for change in report.function_changes:
        function = change.new_function or change.old_function
        if function is None:
            raise ValueError(f"function change {change.function_id!r} has no source data")
        key = f"docs/{Path(function.source_id).with_suffix('.md').as_posix()}"
        grouped.setdefault(key, []).append(change)
    return {
        key: tuple(sorted(items, key=lambda item: item.function_id))
        for key, items in sorted(grouped.items())
    }


def read_document(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""
