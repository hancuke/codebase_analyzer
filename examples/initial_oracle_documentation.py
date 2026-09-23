"""Generate and incrementally update documentation for Oracle PL/SQL."""

from __future__ import annotations

import tempfile
from collections import defaultdict
from pathlib import Path

from change_analyzer import EntryChange, analyze_changes, entry_changes
from codegraph import OraclePlsqlAnalyzer, SourceFile
from documentation_example_support import (
    DemoLlmClient,
    DocumentPublisher,
    document_results_for_entries,
    write_source,
)
from filetracker import ChangeSet, FileTracker


_PLSQL_SUFFIXES = {".pkb", ".pks", ".pls", ".sql"}


def _working_sources(source_root: Path) -> tuple[SourceFile, ...]:
    """Build a complete PL/SQL working snapshot from the current source tree."""
    sources = []
    for path in sorted(source_root.rglob("*")):
        if not path.is_file() or path.suffix.casefold() not in _PLSQL_SUFFIXES:
            continue
        sources.append(
            SourceFile(
                source_id=path.relative_to(source_root).as_posix(),
                content=path.read_text(encoding="utf-8"),
                language="plsql",
            )
        )
    return tuple(sorted(sources, key=lambda source: source.source_id))


def _document_key(source_id: str) -> str:
    return f"docs/{Path(source_id).with_suffix('.md').as_posix()}"


def _entry_changes_for_scan(
    source_root: Path,
    change_set: ChangeSet,
) -> tuple[EntryChange, ...]:
     
    report = analyze_changes(
        change_set,
        _working_sources(source_root),
        [OraclePlsqlAnalyzer()] 
    )
    return entry_changes(report)


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
        changes = _entry_changes_for_scan(source_root, change_set)

        print("Initial PL/SQL entries:", [change.entry_id for change in changes])
        updated_document_keys = _publish_documents(changes, publisher, llm)
        print("Initially updated documents:", updated_document_keys)
        tracker.commit(
            message="initial PL/SQL documentation published",
            expected_revision=change_set.working_revision,
            expected_baseline_revision=change_set.baseline_revision,
        )

        write_source(
            source_root,
            "audit.pkb",
            "CREATE OR REPLACE PACKAGE BODY audit_pkg AS\n"
            "    PROCEDURE write_log(p_id NUMBER) IS\n"
            "    BEGIN\n"
            "        INSERT INTO audit_log(id) VALUES (p_id);\n"
            "    END write_log;\n"
            "END audit_pkg;\n",
        )

        change_set = tracker.scan()
        changes = _entry_changes_for_scan(source_root, change_set)
        print(
            "Entries affected by audit change:",
            [change.entry_id for change in changes],
        )
        incrementally_updated_keys = _publish_documents(changes, publisher, llm)
        print("Incrementally updated documents:", incrementally_updated_keys)
        tracker.commit(
            message="incremental PL/SQL documentation published",
            expected_revision=change_set.working_revision,
            expected_baseline_revision=change_set.baseline_revision,
        )

        for document_key in sorted(
            {*updated_document_keys, *incrementally_updated_keys}
        ):
            document = document_root / document_key
            print(document.relative_to(document_root))
            print(document.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
