"""Demonstrate caller-driven document processing and durable checkpoints."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence,Mapping
from pathlib import Path 
 

from change_analyzer import FunctionChange, analyze_changes
from codegraph import OraclePlsqlAnalyzer, SourceFile
from filetracker import ChangeSet
from change_analyzer import FunctionChange
from document_updater import DocumentResult, ResultKind, apply_result
from document_workbench import Workbench
from filetracker import FileTracker


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



def _write_initial_sources(source_root: Path) -> None:
    (source_root / "orders.pkb").write_text(
        "CREATE OR REPLACE PACKAGE BODY orders AS\n"
        "  PROCEDURE save_order(p_id NUMBER) IS\n"
        "  BEGIN\n"
        "    INSERT INTO orders_log(id) VALUES (p_id);\n"
        "  END save_order;\n"
        "END orders;\n",
        encoding="utf-8",
    )
    (source_root / "customers.pkb").write_text(
        "CREATE OR REPLACE PACKAGE BODY customers AS\n"
        "  PROCEDURE refresh_customer(p_id NUMBER) IS\n"
        "  BEGIN\n"
        "    UPDATE customer SET checked = 1 WHERE id = p_id;\n"
        "  END refresh_customer;\n"
        "END customers;\n",
        encoding="utf-8",
    )


def _write_changed_sources(source_root: Path) -> None:
    (source_root / "orders.pkb").write_text(
        "CREATE OR REPLACE PACKAGE BODY orders AS\n"
        "  PROCEDURE save_order(p_id NUMBER) IS\n"
        "  BEGIN\n"
        "    INSERT INTO orders_log(id, status) VALUES (p_id, 'saved');\n"
        "  END save_order;\n"
        "END orders;\n",
        encoding="utf-8",
    )
    (source_root / "customers.pkb").write_text(
        "CREATE OR REPLACE PACKAGE BODY customers AS\n"
        "  PROCEDURE refresh_customer(p_id NUMBER) IS\n"
        "  BEGIN\n"
        "    UPDATE customer SET checked = 1, checked_at = SYSDATE "
        "WHERE id = p_id;\n"
        "  END refresh_customer;\n"
        "END customers;\n",
        encoding="utf-8",
    )


def generate_document(changes: Sequence[FunctionChange], markdown: str) -> bytes:
    """Render newly analyzed changes; a deterministic renderer stands in for the LLM."""
    for change in changes:
        function = change.new_function
        kind = ResultKind.DELETE if function is None else ResultKind.UPSERT
        body = None
        if function is not None:
            body = f"## {function.name}\n\n```sql\n{function.source.rstrip()}\n```\n"
        result = DocumentResult(
            fragment_id=f"function:{change.function_id}", kind=kind, markdown=body
        )
        markdown = apply_result(markdown, result)
    return markdown.encode("utf-8")


def run_batch(
    source_root: Path,
    document_root: Path,
    workbench_root: Path,
    tracker: FileTracker,
    *,
    process: Callable[[str, Sequence[FunctionChange], str], bytes] | None = None,
) -> None:
    """Reanalyze, skip successful generation, and explicitly publish the results."""
    with Workbench.open(workbench_root, protected_roots=(source_root,)) as workbench:
        changes = tracker.scan()
        for key, function_changes in analyze_documents(source_root, changes).items():
            if workbench.contains(key):
                continue
            markdown = read_document(document_root / key)
            content = (
                generate_document(function_changes, markdown) if process is None
                else process(key, function_changes, markdown)
            )
            workbench.save(key, content)
        workbench.publish(document_root)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="document-workbench-mvp-") as temp:
        project_root = Path(temp)
        source_root = project_root / "src"
        run_root = project_root / ".workbench" / "run-1"
        source_root.mkdir()

        _write_initial_sources(source_root)
        tracker = FileTracker(str(source_root))
        baseline = tracker.scan()
        tracker.commit(
            message="sample baseline",
            expected_revision=baseline.working_revision,
            expected_baseline_revision=baseline.baseline_revision,
        )
        _write_changed_sources(source_root)

        def interrupt(key: str, changes: Sequence[FunctionChange], markdown: str) -> bytes:
            if key == "docs/orders.md":
                raise RuntimeError("simulated interruption")
            print("Processed:", key)
            return generate_document(changes, markdown)

        print("First pass: fail while processing the second document.")
        try:
            run_batch(source_root, project_root, run_root, tracker, process=interrupt)
        except RuntimeError as error:
            if str(error) != "simulated interruption":
                raise
            print(error)

        print("\nResume: skip saved results and finish pending documents.")
        run_batch(source_root, project_root, run_root, tracker)
        print("Workbench removed:", not run_root.exists())
        print("Baseline remains unchanged:", tracker.scan().has_changes)


if __name__ == "__main__":
    main()
