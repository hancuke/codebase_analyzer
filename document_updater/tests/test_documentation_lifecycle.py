from __future__ import annotations

import sys
from pathlib import Path

examples_path = Path(__file__).resolve().parents[2] / "examples"
if str(examples_path) not in sys.path:
    sys.path.insert(0, str(examples_path))

from document_updater import DocumentAction
from documentation_lifecycle import create_vba_lifecycle, write


def test_vba_lifecycle_supports_initial_and_incremental_sync(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    document_root = tmp_path / "published"
    source_root.mkdir()
    document_root.mkdir()
    write(
        source_root,
        "frmOrder.frm",
        "Private Sub bSave_Click()\n"
        "    Call SaveOrder\n"
        "End Sub\n",
    )
    write(
        source_root,
        "modBusiness.bas",
        "Public Sub SaveOrder()\n"
        "End Sub\n",
    )
    lifecycle = create_vba_lifecycle(source_root, document_root)

    initial = lifecycle.synchronize()

    assert [plan.action for plan in initial.plans] == [DocumentAction.CREATE]
    document_path = document_root / "docs" / "frmOrder.md"
    assert document_path.is_file()
    assert "vba:frmOrder:bSave_Click" in document_path.read_text(
        encoding="utf-8"
    )

    write(
        source_root,
        "modBusiness.bas",
        "Public Sub SaveOrder()\n"
        "    result = 1\n"
        "End Sub\n",
    )
    update = lifecycle.synchronize()

    assert [plan.action for plan in update.plans] == [DocumentAction.UPDATE]
    assert lifecycle.tracker.scan().files == ()
