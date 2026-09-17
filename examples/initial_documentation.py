"""Generate documentation from the first scan of an Access/VBA source tree."""

from __future__ import annotations

import tempfile
from pathlib import Path

from documentation_example_support import (
    affected_entries_for_form,
    DemoLlmClient,
    DocumentPublisher,
    document_key_for_form,
    document_results_for_entries,
    form_source_ids_to_analyze,
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
        change_set = tracker.scan()
        publisher = DocumentPublisher(document_root)
        llm = DemoLlmClient()

        form_source_ids = form_source_ids_to_analyze(source_root, change_set)
        changes_by_form = {}
        for form_source_id in form_source_ids:
            changes = affected_entries_for_form(
                source_root,
                change_set,
                form_source_id,
            )
            if changes:
                changes_by_form[form_source_id] = changes
        print("Forms to document:", list(changes_by_form))
        updated_document_keys = []
        for form_source_id, changes in changes_by_form.items():
            document_key = document_key_for_form(form_source_id)
            results = document_results_for_entries(
                changes,
                document_key,
                publisher,
                llm,
            )
            if publisher.publish(document_key, results):
                updated_document_keys.append(document_key)
        print("Updated documents:", updated_document_keys)
        tracker.commit(
            message="initial documentation published",
            expected_revision=change_set.working_revision,
            expected_baseline_revision=change_set.baseline_revision,
        )

        document = document_root / "docs" / "Form_1.md"
        print(document.relative_to(document_root))
        print(document.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
