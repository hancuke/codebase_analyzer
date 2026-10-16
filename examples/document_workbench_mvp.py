"""Resume fragment tasks across A/B phases, assemble documents, and publish.

Tasks merge in per-document save order, preserved across recovery.
No original-document snapshot is captured. The caller must keep sources, original
documents, and generation configuration unchanged throughout a batch and recovery.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Sequence,Mapping
from pathlib import Path 
 

from change_analyzer import FunctionChange, analyze_changes
from codegraph import OraclePlsqlAnalyzer, SourceFile
from filetracker import ChangeSet
from change_analyzer import FunctionChange
from document_updater import DocumentResult, ResultKind
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


def generate_fragments(changes: Sequence[FunctionChange]) -> tuple[DocumentResult, ...]:
    """Generate fragment results; a deterministic renderer stands in for the LLM."""
    results = []
    for change in changes:
        function = change.new_function
        kind = ResultKind.DELETE if function is None else ResultKind.UPSERT
        body = None
        if function is not None:
            body = f"## {function.name}\n\n```sql\n{function.source.rstrip()}\n```\n"
        results.append(
            DocumentResult(
                fragment_id=f"function:{change.function_id}", kind=kind, markdown=body
            )
        )
    return tuple(results)
def main() -> None:
    with tempfile.TemporaryDirectory(prefix="document-workbench-mvp-") as temp:
        project_root = Path(temp)
        source_root = project_root / "src"
        document_root = project_root / "documents"
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

        def task_a(workbench: Workbench, *, interrupt: bool = False) -> None:
            for key, changes in analyze_documents(source_root, tracker.scan()).items():
                for change in changes:
                    task_id = f"A:{change.function_id}"
                    if workbench.contains_task(key, task_id):
                        continue
                    if interrupt and key == "docs/orders.md":
                        raise RuntimeError("simulated interruption")
                    workbench.save_task(key, task_id, generate_fragments((change,)))
                    print("Processed:", task_id)

        print("Task A: fail after committing the first task's fragments.")
        workbench = Workbench.open(document_root / ".workbench", protected_roots=(source_root,))
        try:
            task_a(workbench, interrupt=True)
        except RuntimeError as error:
            if str(error) != "simulated interruption":
                raise
            print(error)

        print("\nResume A, then run B against the same sources and documents.")
        workbench.close()
        workbench = Workbench.open(document_root / ".workbench", protected_roots=(source_root,))
        task_a(workbench)
        for key in analyze_documents(source_root, tracker.scan()):
            task_id = f"B:{key}"
            if not workbench.contains_task(key, task_id):
                workbench.save_task(key, task_id, (DocumentResult(
                    "summary", ResultKind.UPSERT, "Summary supplied by task B."
                ),))

        workbench.publish(document_root)
        print("Workbench removed:", not (document_root / ".workbench").exists())
        print("Baseline remains unchanged:", tracker.scan().has_changes)


if __name__ == "__main__":
    main()
