"""Generate documentation from the first scan of an Oracle PL/SQL source tree."""

from __future__ import annotations

import tempfile
from collections import defaultdict
from pathlib import Path

from change_analyzer import analyze_changes, entry_changes
from codegraph import OraclePlsqlAnalyzer, SourceFile
from documentation_example_support import (
    DemoLlmClient,
    DocumentPublisher,
    document_results_for_entries,
    write_source,
)
from filetracker import ChangeStatus, FileTracker


_PLSQL_SUFFIXES = {".pkb", ".pks", ".pls", ".sql"}


def _working_sources(change_set) -> tuple[SourceFile, ...]:
    """Build the complete PL/SQL working snapshot captured by the scan."""
    sources = []
    for change in change_set.files:
        if change.status is ChangeStatus.DELETED:
            continue
        if change.path.suffix.casefold() not in _PLSQL_SUFFIXES:
            continue
        if not change.working_content.is_text:
            raise ValueError(f"PL/SQL source is not readable as text: {change.path}")
        sources.append(
            SourceFile(
                source_id=change.path.as_posix(),
                content=change.working_content.text or "",
                language="plsql",
            )
        )
    return tuple(sorted(sources, key=lambda source: source.source_id))


def _document_key(source_id: str) -> str:
    return f"docs/{Path(source_id).with_suffix('.md').as_posix()}"


def _publish_documents(
    changes,
    publisher: DocumentPublisher,
    llm: DemoLlmClient,
) -> tuple[str, ...]:
    changes_by_document = defaultdict(list)
    for change in changes:
        if change.new_entry is None or change.new_context is None:
            continue
        source_id = next(
            function.source_id
            for function in change.new_context.functions
            if function.id == change.entry_id
        )
        changes_by_document[_document_key(source_id)].append(change)

    updated = []
    for document_key, document_changes in sorted(changes_by_document.items()):
        results = document_results_for_entries(
            sorted(document_changes, key=lambda change: change.entry_id),
            document_key,
            publisher,
            llm,
        )
        if publisher.publish(document_key, results):
            updated.append(document_key)
    return tuple(updated)


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        project_root = Path(directory)
        source_root = project_root / "src"
        document_root = project_root / "published"
        source_root.mkdir()

        write_source(
            source_root,
            "orders.pks",
            "CREATE OR REPLACE PACKAGE orders AS\n"
            "    PROCEDURE save_order(p_id NUMBER);\n"
            "END orders;\n",
        )
        write_source(
            source_root,
            "orders.pkb",
            "CREATE OR REPLACE PACKAGE BODY orders AS\n"
            "    PROCEDURE save_order(p_id NUMBER) IS\n"
            "    BEGIN\n"
            "        audit_pkg.write_log(p_id);\n"
            "    END save_order;\n"
            "END orders;\n",
        )
        write_source(
            source_root,
            "audit.pkb",
            "CREATE OR REPLACE PACKAGE BODY audit_pkg AS\n"
            "    PROCEDURE write_log(p_id NUMBER) IS\n"
            "    BEGIN\n"
            "        NULL;\n"
            "    END write_log;\n"
            "END audit_pkg;\n",
        )

        tracker = FileTracker(str(source_root))
        change_set = tracker.scan()
        publisher = DocumentPublisher(document_root)
        llm = DemoLlmClient()
        sources = _working_sources(change_set)
        source_languages = {
            change.path.as_posix(): "plsql" for change in change_set.files
        }
        report = analyze_changes(
            change_set,
            sources,
            [OraclePlsqlAnalyzer()],
            source_languages=source_languages,
        )
        changes = entry_changes(report)

        print("PL/SQL entries to document:", [change.entry_id for change in changes])
        updated_document_keys = _publish_documents(changes, publisher, llm)
        print("Updated documents:", updated_document_keys)
        tracker.commit(
            message="initial PL/SQL documentation published",
            expected_revision=change_set.working_revision,
            expected_baseline_revision=change_set.baseline_revision,
        )

        for document_key in updated_document_keys:
            document = document_root / document_key
            print(document.relative_to(document_root))
            print(document.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
