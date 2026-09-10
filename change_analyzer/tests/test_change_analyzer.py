from __future__ import annotations

from pathlib import Path

from change_analyzer import ChangeType, analyze_changes, cluster_impacts
from codegraph import SourceFile, VbaAnalyzer
from filetracker import FileTracker


def _write(root: Path, name: str, content: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _working_sources(root: Path) -> tuple[SourceFile, ...]:
    return tuple(
        SourceFile(path.relative_to(root).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted(root.glob("*"))
        if path.suffix.casefold() in {".bas", ".frm"}
    )


def _baseline_project(root: Path) -> FileTracker:
    _write(
        root,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "    Call CheckPermission\n"
        "    Call SaveOrder\n"
        "End Sub\n",
    )
    _write(
        root,
        "frmCustomer.frm",
        "Private Sub bDelete_Click()\n"
        "    Call CheckPermission\n"
        "End Sub\n",
    )
    _write(
        root,
        "modBusiness.bas",
        "Public Sub CheckPermission()\n"
        "    result = 1\n"
        "End Sub\n\n"
        "Public Sub SaveOrder()\n"
        "    result = 1\n"
        "End Sub\n\n"
        "Public Sub OrphanTask()\n"
        "    result = 1\n"
        "End Sub\n",
    )
    tracker = FileTracker(str(root))
    tracker.commit(message="baseline")
    return tracker


def test_maps_multi_file_changes_to_each_affected_entry(tmp_path: Path) -> None:
    tracker = _baseline_project(tmp_path)
    _write(
        tmp_path,
        "modBusiness.bas",
        "Public Sub CheckPermission()\n"
        "    result = 2\n"
        "End Sub\n\n"
        "Public Sub SaveOrder()\n"
        "    Call AuditOrder\n"
        "End Sub\n\n"
        "Public Sub AuditOrder()\n"
        "End Sub\n\n"
        "Public Sub OrphanTask()\n"
        "    result = 2\n"
        "End Sub\n",
    )

    change_set = tracker.scan()
    report = analyze_changes(
        change_set,
        _working_sources(tmp_path),
        [VbaAnalyzer()],
    )

    changes = {
        change.function_id: change.change_type
        for change in report.function_changes
    }
    assert changes == {
        "vba:modBusiness:AuditOrder": ChangeType.ADDED,
        "vba:modBusiness:CheckPermission": ChangeType.MODIFIED,
        "vba:modBusiness:OrphanTask": ChangeType.MODIFIED,
        "vba:modBusiness:SaveOrder": ChangeType.MODIFIED,
    }
    impacts = {
        impact.entry_id: {
            evidence.changed_function_id for evidence in impact.evidence
        }
        for impact in report.entry_impacts
    }
    assert impacts == {
        "vba:frmCustomer:bDelete_Click": {
            "vba:modBusiness:CheckPermission",
        },
        "vba:frmOrder:bSave_Click": {
            "vba:modBusiness:AuditOrder",
            "vba:modBusiness:CheckPermission",
            "vba:modBusiness:SaveOrder",
        },
    }
    assert [
        change.function_id for change in report.unassigned_changes
    ] == ["vba:modBusiness:OrphanTask"]
    assert report.call_edge_changes == (
        report.call_edge_changes[0],
    )
    assert report.call_edge_changes[0].source_function_id == (
        "vba:modBusiness:SaveOrder"
    )
    assert report.call_edge_changes[0].target_function_id == (
        "vba:modBusiness:AuditOrder"
    )
    assert cluster_impacts(report)[0].entry_ids == (
        "vba:frmCustomer:bDelete_Click",
        "vba:frmOrder:bSave_Click",
    )


def test_deleted_dependency_still_impacts_entry_from_old_graph(
    tmp_path: Path,
) -> None:
    tracker = _baseline_project(tmp_path)
    _write(
        tmp_path,
        "modBusiness.bas",
        "Public Sub SaveOrder()\n"
        "    result = 1\n"
        "End Sub\n\n"
        "Public Sub OrphanTask()\n"
        "    result = 1\n"
        "End Sub\n",
    )

    report = analyze_changes(
        tracker.scan(),
        _working_sources(tmp_path),
        [VbaAnalyzer()],
    )

    permission = next(
        change
        for change in report.function_changes
        if change.function_id == "vba:modBusiness:CheckPermission"
    )
    assert permission.change_type is ChangeType.DELETED
    affected = {
        impact.entry_id
        for impact in report.entry_impacts
        if any(
            evidence.changed_function_id == permission.function_id
            for evidence in impact.evidence
        )
    }
    assert affected == {
        "vba:frmCustomer:bDelete_Click",
        "vba:frmOrder:bSave_Click",
    }
