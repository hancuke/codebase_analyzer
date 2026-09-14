from __future__ import annotations

import sys
from pathlib import Path


examples_path = Path(__file__).resolve().parents[2] / "examples"
if str(examples_path) not in sys.path:
    sys.path.insert(0, str(examples_path))

from documentation_lifecycle import main


def test_documentation_lifecycle_runs_end_to_end(capsys) -> None:
    main()

    output = capsys.readouterr().out
    assert "Affected entries: ['vba:frmOrder:bSave_Click']" in output
    assert "This generated fragment documents `vba:frmOrder:bSave_Click`." in output
    assert "Document exists: False" in output
