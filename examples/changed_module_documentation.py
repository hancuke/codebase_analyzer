"""Update only Entries affected by a changed shared Access/VBA module."""

from __future__ import annotations

import tempfile
from pathlib import Path

from documentation_example_support import (
    affected_entries_by_form,
    DemoLlmClient,
    DocumentPublisher,
    publish_documents_for_forms,
    write_source,
)
from filetracker import FileTracker


def main() -> None:
    with tempfile.TemporaryDirectory() as directory:
        project_root = Path(directory)
        source_root = project_root / "src"
        document_root = project_root / "published"
        source_root.mkdir()

        write_source(
            source_root,
            "Form_1.txt",
            "Version =20\n"
            "Begin Form\n"
            '    Caption ="Orders"\n'
            "End\n"
            "CodeBehindForm\n"
            "Private Sub bSave_Click()\n"
            "    Call ValidateOrder\n"
            "End Sub\n",
            encoding="utf-16",
        )
        write_source(
            source_root,
            "Module_1.txt",
            "Public Sub ValidateOrder()\n"
            "    result = 1\n"
            "End Sub\n",
        )

        tracker = FileTracker(str(source_root))
        publisher = DocumentPublisher(document_root)
        llm = DemoLlmClient()

        initial_change_set = tracker.scan()
        initial_changes_by_form = affected_entries_by_form(
            source_root,
            initial_change_set,
        )
        print("Initial forms to document:", list(initial_changes_by_form))
        initial_document_keys = publish_documents_for_forms(
            initial_changes_by_form,
            publisher,
            llm,
        )
        print("Initial documents:", initial_document_keys)
        tracker.commit(
            message="initial source baseline",
            expected_revision=initial_change_set.working_revision,
            expected_baseline_revision=initial_change_set.baseline_revision,
        )

        write_source(
            source_root,
            "Module_1.txt",
            "Public Sub ValidateOrder()\n"
            "    result = 2\n"
            "End Sub\n",
        )
        change_set = tracker.scan()

        changes_by_form = affected_entries_by_form(source_root, change_set)
        print("Affected forms:", list(changes_by_form))
        updated_document_keys = publish_documents_for_forms(
            changes_by_form,
            publisher,
            llm,
        )
        print("Updated documents:", updated_document_keys)
        tracker.commit(
            message="documentation updated",
            expected_revision=change_set.working_revision,
            expected_baseline_revision=change_set.baseline_revision,
        )

        document = document_root / "docs" / "Form_1.md"
        print(document.relative_to(document_root))
        print(document.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
