"""Tests for the filetracker CLI (scan / diff / commit / undo)."""

from __future__ import annotations

from filetracker.cli import main


def _write(root, name, text):
    p = root / name
    p.write_bytes(text.encode("utf-8"))
    return p


def test_cli_scan_commit_undo(tmp_path, capsys):
    _write(tmp_path, "a.py", "x")
    assert main(["scan", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "1 added" in out
    assert "[added] a.py" in out

    # Commit #1.
    assert main(["commit", "--root", str(tmp_path), "-m", "init"]) == 0
    assert "Baseline advanced." in capsys.readouterr().out

    _write(tmp_path, "a.py", "xy")
    assert main(["scan", "--root", str(tmp_path)]) == 0
    assert "1 modified" in capsys.readouterr().out

    # Commit #2 (baseline now == "xy").
    assert main(["commit", "--root", str(tmp_path), "-m", "second"]) == 0
    capsys.readouterr()

    # Move the working file forward again, then undo: baseline rolls back to
    # after commit #1 ("x"), so the change is visible once more as modified.
    _write(tmp_path, "a.py", "xyz")
    assert main(["undo", "--root", str(tmp_path)]) == 0
    assert "Baseline rolled back" in capsys.readouterr().out

    assert main(["scan", "--root", str(tmp_path)]) == 0
    assert "1 modified" in capsys.readouterr().out


def test_cli_undo_nothing(tmp_path, capsys):
    assert main(["undo", "--root", str(tmp_path)]) == 0
    assert "Nothing to undo." in capsys.readouterr().out


def test_cli_diff_shows_file_level_unified_diff(tmp_path, capsys):
    _write(tmp_path, "m.py", "value = 1\n")
    assert main(["commit", "--root", str(tmp_path), "-m", "init"]) == 0
    capsys.readouterr()
    _write(tmp_path, "m.py", "value = 2\n")

    assert main(["diff", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out

    assert "[modified] m.py" in out
    assert "--- a/m.py" in out
    assert "+++ b/m.py" in out
    assert "-value = 1" in out
    assert "+value = 2" in out


def test_cli_diff_shows_added_and_deleted_files(tmp_path, capsys):
    _write(tmp_path, "deleted.txt", "old content\n")
    assert main(["commit", "--root", str(tmp_path), "-m", "init"]) == 0
    capsys.readouterr()
    (tmp_path / "deleted.txt").unlink()
    _write(tmp_path, "added.txt", "new content\n")

    assert main(["diff", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out

    assert "[added] added.txt" in out
    assert "[deleted] deleted.txt" in out
    assert "+new content" in out
    assert "-old content" in out


def test_cli_diff_reports_clean_worktree(tmp_path, capsys):
    _write(tmp_path, "m.py", "value = 1\n")
    assert main(["commit", "--root", str(tmp_path), "-m", "init"]) == 0
    capsys.readouterr()

    assert main(["diff", "--root", str(tmp_path)]) == 0

    assert capsys.readouterr().out == "No file changes detected.\n"


def test_cli_diff_does_not_render_binary_content_as_text(tmp_path, capsys):
    (tmp_path / "data.bin").write_bytes(b"\x00old")
    assert main(["commit", "--root", str(tmp_path)]) == 0
    capsys.readouterr()
    (tmp_path / "data.bin").write_bytes(b"\x00new")

    assert main(["diff", "--root", str(tmp_path)]) == 0

    assert "Binary or undecodable content; no text diff available." in capsys.readouterr().out
