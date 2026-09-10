from __future__ import annotations

from codegraph import SourceFile
from filetracker import ChangeSet, ChangeStatus

from .models import SnapshotIssue, SourceSnapshots


def build_source_snapshots(
    change_set: ChangeSet,
    working_sources: tuple[SourceFile, ...] | list[SourceFile],
) -> SourceSnapshots:
    """Reconstruct baseline and working source snapshots from one scan.

    ``working_sources`` supplies the complete current project. Changed files are
    replaced with the immutable content captured by ``FileTracker.scan()`` so
    both snapshots remain tied to the scan revisions.
    """

    baseline = {source.source_id: source for source in working_sources}
    working = dict(baseline)
    issues: list[SnapshotIssue] = []

    for file_change in change_set.files:
        source_id = file_change.path.as_posix()
        language = working.get(source_id).language if source_id in working else None

        if file_change.status is ChangeStatus.ADDED:
            baseline.pop(source_id, None)
        elif file_change.baseline_content.is_text:
            baseline[source_id] = SourceFile(
                source_id=source_id,
                content=file_change.baseline_content.text or "",
                language=language,
            )
        else:
            baseline.pop(source_id, None)
            issues.append(
                SnapshotIssue(
                    source_id=source_id,
                    side="baseline",
                    reason=file_change.baseline_content.availability.value,
                )
            )

        if file_change.status is ChangeStatus.DELETED:
            working.pop(source_id, None)
        elif file_change.working_content.is_text:
            working[source_id] = SourceFile(
                source_id=source_id,
                content=file_change.working_content.text or "",
                language=language,
            )
        else:
            working.pop(source_id, None)
            issues.append(
                SnapshotIssue(
                    source_id=source_id,
                    side="working",
                    reason=file_change.working_content.availability.value,
                )
            )

    return SourceSnapshots(
        baseline=tuple(baseline[key] for key in sorted(baseline)),
        working=tuple(working[key] for key in sorted(working)),
        issues=tuple(sorted(issues, key=lambda item: (item.source_id, item.side))),
    )
