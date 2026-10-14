"""Demonstrate a resumable FileTracker -> change_analyzer -> document_updater flow."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from change_analyzer import FunctionChange, analyze_changes
from codegraph import OraclePlsqlAnalyzer, SourceFile
from document_updater import DocumentResult, ResultKind, apply_result
from document_workbench import DocumentRun
from filetracker import (
    ChangeSet,
    ChangeStatus,
    ContentAvailability,
    ContentSnapshot,
    FileChange,
    FileState,
    FileTracker,
)

_RUN_INPUT_SNAPSHOT_FILENAME = "snapshot.json"


def _document_key(source_id: str) -> str:
    return f"docs/{Path(source_id).with_suffix('.md').as_posix()}"


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


def _state_to_dict(state: FileState | None) -> dict[str, object] | None:
    if state is None:
        return None
    return {
        "path": state.path.as_posix(),
        "exists": state.exists,
        "size": state.size,
        "mtime": state.mtime,
        "sha256": state.sha256,
    }


def _state_from_dict(value: dict[str, object] | None) -> FileState | None:
    if value is None:
        return None
    return FileState(
        path=Path(str(value["path"])),
        exists=bool(value["exists"]),
        size=value["size"],
        mtime=value["mtime"],
        sha256=value["sha256"],
    )


def _change_set_to_dict(change_set: ChangeSet) -> dict[str, object]:
    files = []
    for change in change_set.files:
        files.append(
            {
                "path": change.path.as_posix(),
                "status": change.status.value,
                "baseline_state": _state_to_dict(change.baseline_state),
                "working_state": _state_to_dict(change.working_state),
                "baseline_content": {
                    "availability": change.baseline_content.availability.value,
                    "text": change.baseline_content.text,
                },
                "working_content": {
                    "availability": change.working_content.availability.value,
                    "text": change.working_content.text,
                },
            }
        )
    return {
        "baseline_revision": change_set.baseline_revision,
        "working_revision": change_set.working_revision,
        "files": files,
    }


def _change_set_from_dict(value: dict[str, Any]) -> ChangeSet:
    files = []
    for file_change in value["files"]:
        baseline = file_change["baseline_content"]
        working = file_change["working_content"]
        files.append(
            FileChange(
                path=Path(file_change["path"]),
                status=ChangeStatus(file_change["status"]),
                baseline_state=_state_from_dict(file_change["baseline_state"]),
                working_state=_state_from_dict(file_change["working_state"]),
                baseline_content=ContentSnapshot(
                    availability=ContentAvailability(baseline["availability"]),
                    text=baseline["text"],
                ),
                working_content=ContentSnapshot(
                    availability=ContentAvailability(working["availability"]),
                    text=working["text"],
                ),
            )
        )
    return ChangeSet(
        files=tuple(files),
        baseline_revision=str(value["baseline_revision"]),
        working_revision=str(value["working_revision"]),
    )


def _capture_run_inputs(
    inputs_root: Path,
    source_root: Path,
    document_root: Path,
    tracker: FileTracker,
) -> None:
    change_set = tracker.scan()
    sources = [
        {
            "source_id": path.relative_to(source_root).as_posix(),
            "content": path.read_text(encoding="utf-8"),
            "language": "plsql",
        }
        for path in sorted(source_root.rglob("*.pkb"))
        if path.is_file()
    ]
    documents = {
        path.relative_to(document_root).as_posix(): path.read_text(
            encoding="utf-8"
        )
        for path in sorted(document_root.rglob("*.md"))
        if path.is_file()
    }
    snapshot = {
        "change_set": _change_set_to_dict(change_set),
        "working_sources": sources,
        "documents": documents,
    }
    (inputs_root / _RUN_INPUT_SNAPSHOT_FILENAME).write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_snapshot(snapshot_root: Path) -> dict[str, Any]:
    return json.loads(
        (snapshot_root / _RUN_INPUT_SNAPSHOT_FILENAME).read_text(encoding="utf-8")
    )


def _function_result(change: FunctionChange) -> DocumentResult:
    function = change.new_function
    if function is None:
        return DocumentResult(
            fragment_id=f"function:{change.function_id}",
            kind=ResultKind.DELETE,
        )
    markdown = (
        f"## {function.name}\n\n"
        f"```sql\n{function.source.rstrip()}\n```\n"
    )
    return DocumentResult(
        fragment_id=f"function:{change.function_id}",
        kind=ResultKind.UPSERT,
        markdown=markdown,
    )


def run_pending_documents(
    run_root: Path,
    *,
    stop_after: int | None = None,
) -> tuple[str, ...]:
    completed_now = []
    with DocumentRun.open(run_root) as run:
        snapshot = _load_snapshot(run.snapshot_root)
        change_set = _change_set_from_dict(snapshot["change_set"])
        working_sources = tuple(
            SourceFile(
                source_id=item["source_id"],
                content=item["content"],
                language=item["language"],
            )
            for item in snapshot["working_sources"]
        )

        # FileTracker 的固定 ChangeSet 与完整 working sources 一起交给分析器。
        report = analyze_changes(
            change_set,
            working_sources,
            [OraclePlsqlAnalyzer()],
        )

        changes_by_document: dict[str, list[FunctionChange]] = {}
        for change in report.function_changes:
            function = change.new_function or change.old_function
            if function is None:
                raise ValueError(
                    f"function change {change.function_id!r} has no source data"
                )
            document_key = _document_key(function.source_id)
            changes_by_document.setdefault(document_key, []).append(change)

        run.register_units(changes_by_document)
        documents = snapshot["documents"]

        for unit_id in run.pending_units():
            markdown = documents.get(unit_id, "")
            for change in sorted(
                changes_by_document[unit_id],
                key=lambda item: item.function_id,
            ):
                # This deterministic renderer stands in for the caller's generator.
                markdown = apply_result(markdown, _function_result(change))

            output_path = run.prepare_unit_output(unit_id)
            output_path.write_text(markdown, encoding="utf-8")
            run.mark_unit_completed(unit_id)
            completed_now.append(unit_id)
            print(f"Prepared {unit_id}")

            if stop_after is not None and len(completed_now) >= stop_after:
                break

        print("Still pending:", list(run.pending_units()))
    return tuple(completed_now)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="document-workbench-mvp-") as temp:
        project_root = Path(temp)
        source_root = project_root / "src"
        document_root = project_root
        run_root = project_root / ".workbench" / "run-1"
        source_root.mkdir()
        (document_root / "docs").mkdir()

        _write_initial_sources(source_root)
        tracker = FileTracker(str(source_root))
        baseline = tracker.scan()
        tracker.commit(
            message="sample baseline",
            expected_revision=baseline.working_revision,
            expected_baseline_revision=baseline.baseline_revision,
        )

        _write_changed_sources(source_root)
        def capture_initial_snapshot(inputs_root: Path) -> None:
            _capture_run_inputs(inputs_root, source_root, document_root, tracker)

        DocumentRun.open_or_create(
            run_root, capture_inputs=capture_initial_snapshot
        ).close()

        print("First pass: stop after one document to simulate interruption.")
        run_pending_documents(run_root, stop_after=1)

        print("\nResume: reopen the same document run and finish pending documents.")
        run_pending_documents(run_root)

        with DocumentRun.open(run_root) as run:
            change_set = _change_set_from_dict(
                _load_snapshot(run.snapshot_root)["change_set"]
            )
            for unit_id in run.status().unit_ids:
                published_path = document_root / unit_id
                published_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(run.output_path(unit_id), published_path)
                print(f"Published {published_path.relative_to(project_root)}")

        tracker.commit(
            message="sample documents published",
            expected_revision=change_set.working_revision,
            expected_baseline_revision=change_set.baseline_revision,
        )


if __name__ == "__main__":
    main()
