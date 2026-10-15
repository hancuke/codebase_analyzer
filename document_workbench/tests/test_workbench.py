from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

import document_workbench.workbench as storage
from document_workbench import Workbench, WorkbenchError


def test_failure_and_reopen_skip_success_with_rebuilt_tasks(tmp_path: Path) -> None:
    root = tmp_path / "run"
    processed = []
    with pytest.raises(RuntimeError, match="processing failed"):
        with Workbench.open(root) as workbench:
            for key in ("docs/A.md", "docs/B.md"):
                processed.append(key)
                if key == "docs/B.md":
                    raise RuntimeError("processing failed")
                workbench.save(key, "# A")
            pytest.fail("must stop")
    with Workbench.open(root) as workbench:
        assert workbench.contains("docs/A.md")
        assert not workbench.contains("docs/B.md")
        assert workbench.results() == {"docs/A.md": "# A"}
        workbench.save("docs/B.md", "# B")
        assert workbench.results() == {"docs/A.md": "# A", "docs/B.md": "# B"}
        with pytest.raises(TypeError):
            workbench.results()["docs/A.md"] = "changed"
    assert processed == ["docs/A.md", "docs/B.md"]
    state = json.loads((root / "state.json").read_bytes())
    assert set(state) == {"schema_version", "completed", "tasks", "frozen"}
    assert state["schema_version"] == 7
    assert list(root.glob("*.json")) == [root / "state.json"]
    with Workbench.open(root) as workbench:
        assert tuple(workbench.results()) == ("docs/A.md", "docs/B.md")


def test_another_process_resumes_committed_documents(tmp_path: Path) -> None:
    root = tmp_path / "run"
    first = subprocess.run(
        [
            sys.executable, "-c",
            "import sys\n"
            "from document_workbench import Workbench\n"
            "with Workbench.open(sys.argv[1]) as w:\n"
            "    w.save('a', 'A')\n"
            "    raise RuntimeError('interrupted')\n",
            str(root),
        ],
        capture_output=True, text=True, check=False,
    )
    assert first.returncode != 0
    assert "RuntimeError: interrupted" in first.stderr
    second = subprocess.run(
        [
            sys.executable, "-c",
            "import sys\n"
            "from document_workbench import Workbench\n"
            "with Workbench.open(sys.argv[1]) as w:\n"
            "    assert w.contains('a') and not w.contains('b')\n"
            "    w.save('b', 'B')\n"
            "    assert w.results() == {'a': 'A', 'b': 'B'}\n",
            str(root),
        ],
        capture_output=True, text=True, check=False,
    )
    assert second.returncode == 0, second.stderr


@pytest.mark.parametrize("content", ["", "\x00\u00ff\r\n", "\u4fdd\u7559\r\n"])
def test_exact_utf8_text_round_trip(tmp_path: Path, content: str) -> None:
    root = tmp_path / "run"
    with Workbench.open(root) as workbench:
        workbench.save("a", content)
        assert workbench.contains("a")
        encoded = content.encode("utf-8")
        assert (root / "outputs/a").read_bytes() == encoded
        state = json.loads((root / "state.json").read_bytes())
        assert state["completed"]["a"]["size_bytes"] == len(encoded)
        assert state["completed"]["a"]["sha256"] == hashlib.sha256(encoded).hexdigest()
    with Workbench.open(root) as workbench:
        assert workbench.results() == {"a": content}


def test_invalid_utf8_result_is_rejected_on_reopen(tmp_path: Path) -> None:
    root = tmp_path / "run"
    with Workbench.open(root) as workbench:
        workbench.save("a", "text")

    encoded = b"\xff"
    (root / "outputs/a").write_bytes(encoded)
    state_path = root / "state.json"
    state = json.loads(state_path.read_bytes())
    state["completed"]["a"] = {
        "size_bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    with pytest.raises(WorkbenchError, match="valid UTF-8"):
        Workbench.open(root)


def test_empty_store_and_invalid_save_have_no_side_effects(tmp_path: Path) -> None:
    with Workbench.open(tmp_path / "run") as workbench:
        assert workbench.results() == {}
        with pytest.raises(TypeError):
            workbench.save("docs/A.md", b"text")
        assert not (tmp_path / "run" / "outputs").exists()
        workbench.save("docs/A.md", "A")
        with pytest.raises(WorkbenchError, match="already completed"):
            workbench.save("docs/A.md", "replacement")


@pytest.mark.parametrize(
    "key", ["", "../escape", "/absolute", "C:/absolute", r"docs\escape",
            "docs//empty", "docs/./dot", "docs/\x00", 1]
)
def test_invalid_keys_do_not_write_results(tmp_path: Path, key: str) -> None:
    with Workbench.open(tmp_path / "run") as workbench:
        with pytest.raises((ValueError, TypeError)):
            workbench.save(key, "result")
        with pytest.raises((ValueError, TypeError)):
            workbench.contains(key)
        assert not (tmp_path / "run/outputs").exists()


@pytest.mark.parametrize(
    "key", ["DOCS/a.md", "docs/A.MD", "docs/a.md/child", "docs", "DOCS/b.md"]
)
def test_colliding_paths_are_rejected_before_writing(tmp_path: Path, key: str) -> None:
    with Workbench.open(tmp_path / "run") as workbench:
        workbench.save("docs/a.md", "A")
        with pytest.raises(ValueError):
            workbench.save(key, "B")
        assert workbench.results() == {"docs/a.md": "A"}


@pytest.mark.parametrize("stage", ["output", "sync", "state", "replace", "directory"])
def test_commit_failure_reopens_at_the_persisted_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    root = tmp_path / "run"
    original_open = Path.open
    original_sync = Workbench._fsync_directory

    def fail_output(path: Path, mode="r", *args, **kwargs):
        if mode == "wb":
            with original_open(path, "wb") as stream:
                stream.write(b"partial")
            raise OSError("output failed")
        return original_open(path, mode, *args, **kwargs)

    def fail_sync(descriptor: int) -> None:
        raise OSError("sync failed")

    def fail_replace(source, target) -> None:
        raise OSError("replace failed")

    def fail_state(value, stream, **kwargs) -> None:
        stream.write("partial")
        raise OSError("state failed")

    def fail_directory(path: Path) -> None:
        if path == root:
            raise OSError("directory failed")
        original_sync(path)

    with Workbench.open(root) as workbench:
        workbench.save("docs/A.md", "A")
        with monkeypatch.context() as patch:
            if stage == "output":
                patch.setattr(Path, "open", fail_output)
            elif stage == "sync":
                patch.setattr(storage.os, "fsync", fail_sync)
            elif stage == "replace":
                patch.setattr(storage.os, "replace", fail_replace)
            elif stage == "state":
                patch.setattr(storage.json, "dump", fail_state)
            else:
                patch.setattr(Workbench, "_fsync_directory", staticmethod(fail_directory))
            with pytest.raises(OSError, match=f"{stage} failed"):
                workbench.save("docs/B.md", "B")
        if stage in {"state", "replace", "directory"}:
            with pytest.raises(WorkbenchError, match="reopen"):
                workbench.contains("docs/A.md")
    with Workbench.open(root) as workbench:
        if stage != "directory":
            assert not workbench.contains("docs/B.md")
            workbench.save("docs/B.md", "retried B")
        assert workbench.results()["docs/A.md"] == "A"


def test_initial_state_failure_and_known_temporary_are_retryable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "run"

    def fail_replace(source, target) -> None:
        raise OSError("initial commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "replace", fail_replace)
        with pytest.raises(OSError):
            Workbench.open(root)
    assert not (root / "state.json").exists()
    temporary = root / ".state-interrupted.tmp"
    temporary.write_bytes(b"partial")
    with Workbench.open(root):
        assert not temporary.exists()


@pytest.mark.parametrize("damage", ["missing", "changed", "symlink"])
def test_successful_result_damage_is_not_skipped(tmp_path: Path, damage: str) -> None:
    root = tmp_path / "run"
    with Workbench.open(root) as workbench:
        workbench.save("docs/A.md", "A")
        output = root / "outputs/docs/A.md"
        output.unlink()
        if damage == "changed":
            output.write_bytes(b"changed")
        elif damage == "symlink":
            outside = tmp_path / "outside"
            outside.write_bytes(b"A")
            output.symlink_to(outside)
        with pytest.raises(WorkbenchError):
            workbench.contains("docs/A.md")
    with pytest.raises(WorkbenchError):
        Workbench.open(root)


@pytest.mark.parametrize(
    "value",
    [
        b"not json", b"\xff",
        b'{"schema_version":5,"schema_version":5}',
        b'{"schema_version":5,"completed":{"a":{},"a":{}}}',
        b'{"schema_version":5,"completed":{"a":{"size_bytes":NaN,"sha256":"invalid"}}}',
    ],
)
def test_invalid_json_is_not_reset_on_repeated_open(tmp_path: Path, value: bytes) -> None:
    root = tmp_path / "run"
    root.mkdir()
    (root / "state.json").write_bytes(value)
    for _ in range(2):
        with pytest.raises(WorkbenchError, match="invalid"):
            Workbench.open(root)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True), ("schema_version", 4),
        ("completed", {"../escape": {"size_bytes": 0, "sha256": "0" * 64}}),
        ("completed", None), ("documents", {}),
        ("completed", {"docs/A.md": {"size_bytes": True, "sha256": "0" * 64}}),
        ("completed", {"docs/A.md": {"size_bytes": 0, "sha256": "invalid"}}),
    ],
)
def test_invalid_persisted_structure_is_rejected(tmp_path: Path, field: str, value: object) -> None:
    root = tmp_path / "run"
    with Workbench.open(root):
        pass
    path = root / "state.json"
    state = json.loads(path.read_bytes())
    state[field] = value
    path.write_text(json.dumps(state))
    with pytest.raises(WorkbenchError):
        Workbench.open(root)


@pytest.mark.parametrize("name", ["unknown.txt", "outputs", "manifest.json"])
def test_uncommitted_unknown_or_legacy_data_is_not_removed(tmp_path: Path, name: str) -> None:
    root = tmp_path / "run"
    root.mkdir()
    marker = root / name
    marker.write_bytes(b"keep")
    with pytest.raises(WorkbenchError):
        Workbench.open(root)
    assert marker.read_bytes() == b"keep"


@pytest.mark.parametrize("name", [".lock", "state.json", ".state-partial.tmp", "outputs"])
def test_symlinks_are_not_followed(tmp_path: Path, name: str) -> None:
    root = tmp_path / "run"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.write_bytes(b"keep")
    (root / name).symlink_to(outside)
    with pytest.raises(WorkbenchError):
        with Workbench.open(root) as workbench:
            workbench.save("docs/A.md", "A")
    assert outside.read_bytes() == b"keep"


def test_explicit_closed_lifecycle_without_lock_file(tmp_path: Path) -> None:
    root = tmp_path / "run"
    workbench = Workbench.open(root)
    workbench.save("docs/A.md", "A")
    assert not (root / ".lock").exists()
    workbench.close()
    workbench.close()
    for operation in (lambda: workbench.contains("docs/A.md"), workbench.results,
                      lambda: workbench.save("docs/A.md", "A"),
                      lambda: workbench.publish(tmp_path / "documents")):
        with pytest.raises(WorkbenchError, match="closed"):
            operation()
    with Workbench.open(root):
        pass


def test_legacy_lock_file_allows_serial_recovery_and_cleanup(tmp_path: Path) -> None:
    target = tmp_path / "documents"
    root = target / ".workbench"
    workbench = Workbench.open(root)
    workbench.save("a.md", "A")
    workbench.close()
    (root / ".lock").write_bytes(b"\0")
    recovered = Workbench.open(root)
    assert recovered.results() == {"a.md": "A"}
    recovered.publish(target)
    assert (target / "a.md").read_text(encoding="utf-8") == "A"
    assert not root.exists()


def test_independent_document_roots_keep_results_isolated(tmp_path: Path) -> None:
    first_target = tmp_path / "first"
    second_target = tmp_path / "second"
    first = Workbench.open(first_target / ".workbench")
    second = Workbench.open(second_target / ".workbench")
    first.save("a.md", "first")
    second.save("a.md", "second")

    first.publish(first_target)
    assert second.results() == {"a.md": "second"}
    second.publish(second_target)

    assert (first_target / "a.md").read_text(encoding="utf-8") == "first"
    assert (second_target / "a.md").read_text(encoding="utf-8") == "second"
    assert not (first_target / ".workbench").exists()
    assert not (second_target / ".workbench").exists()


def test_unknown_data_in_committed_root_is_preserved(tmp_path: Path) -> None:
    root = tmp_path / "run"
    with Workbench.open(root):
        pass
    marker = root / "unknown.txt"
    marker.write_bytes(b"keep")
    with pytest.raises(WorkbenchError, match="unexpected"):
        Workbench.open(root)
    assert marker.read_bytes() == b"keep"


def test_nested_output_symlink_is_not_followed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    with Workbench.open(tmp_path / "run") as workbench:
        outputs = tmp_path / "run/outputs"
        outputs.mkdir()
        (outputs / "docs").symlink_to(outside, target_is_directory=True)
        with pytest.raises(WorkbenchError, match="real directory"):
            workbench.save("docs/A.md", "A")
    assert not list(outside.iterdir())


def test_publish_copies_results_cleans_store_and_closes_handle(tmp_path: Path) -> None:
    root = tmp_path / "workbench"
    target = tmp_path / "published"
    with Workbench.open(root) as workbench:
        workbench.save("docs/a.md", "保存")
        workbench.save("docs/b.md", "")
        workbench.publish(target)
        with pytest.raises(WorkbenchError, match="closed"):
            workbench.contains("docs/a.md")
    assert not root.exists()
    assert (target / "docs/a.md").read_text(encoding="utf-8") == "保存"
    assert (target / "docs/b.md").read_text(encoding="utf-8") == ""


def test_normal_exit_does_not_publish_or_clean(tmp_path: Path) -> None:
    root = tmp_path / "workbench"
    with Workbench.open(root) as workbench:
        workbench.save("a", "A")
    assert root.exists()
    with Workbench.open(root) as workbench:
        assert workbench.contains("a")


def test_publish_failure_preserves_results_and_original_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "workbench"
    target = tmp_path / "published"
    target.mkdir()
    (target / "a").write_bytes(b"original")
    with Workbench.open(root) as workbench:
        workbench.save("a", "new")
        with monkeypatch.context() as patch:
            def fail_replace(source, destination):
                raise OSError("publish failed")
            patch.setattr(storage.os, "replace", fail_replace)
            with pytest.raises(OSError, match="publish failed"):
                workbench.publish(target)
        assert workbench.contains("a")
        assert (target / "a").read_text(encoding="utf-8") == "original"
        assert not list(target.glob(".publish-*"))
        workbench.publish(target)
    assert not root.exists()
    assert (target / "a").read_text(encoding="utf-8") == "new"


def test_publish_rejects_destination_inside_store_and_symlink(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workbench"
    outside = tmp_path / "outside"
    outside.mkdir()
    target = tmp_path / "published"
    target.mkdir()
    (target / "docs").symlink_to(outside, target_is_directory=True)
    with Workbench.open(root) as workbench:
        workbench.save("docs/a", "A")
        for destination in (root, root / "nested"):
            with pytest.raises(ValueError):
                workbench.publish(destination)
        with pytest.raises(WorkbenchError, match="real directory"):
            workbench.publish(target)
        assert workbench.contains("docs/a")
    assert not list(outside.iterdir())


def test_publish_cannot_overwrite_its_state_through_parent_destination(tmp_path: Path) -> None:
    root = tmp_path / "workbench"
    with Workbench.open(root) as workbench:
        workbench.save("workbench/state.json", "replacement")
        with pytest.raises(ValueError, match="overwrite"):
            workbench.publish(tmp_path)
        assert workbench.contains("workbench/state.json")


def test_cleanup_failure_is_explicit_and_does_not_remove_unknown_data(tmp_path: Path) -> None:
    root = tmp_path / "workbench"
    target = tmp_path / "published"
    with Workbench.open(root) as workbench:
        workbench.save("a", "A")
        marker = root / "outputs/unknown"
        marker.write_bytes(b"keep")
        with pytest.raises(OSError):
            workbench.publish(target)
        assert marker.read_bytes() == b"keep"
        assert (target / "a").read_text(encoding="utf-8") == "A"
        with pytest.raises(WorkbenchError, match="closed"):
            workbench.results()


@pytest.mark.parametrize("location", ["same", "parent", "child"])
def test_protected_directory_overlap_is_rejected_before_creating_store(
    tmp_path: Path, location: str
) -> None:
    protected = tmp_path / "project/src"
    protected.mkdir(parents=True)
    marker = protected / "keep"
    marker.write_bytes(b"keep")
    root = {
        "same": protected,
        "parent": protected.parent,
        "child": protected / "workbench",
    }[location]
    with pytest.raises(ValueError, match="protected"):
        Workbench.open(root, protected_roots=(protected,))
    assert marker.read_bytes() == b"keep"
    assert not (root / ".lock").exists()
    assert not (root / "state.json").exists()
    if location == "child":
        assert not root.exists()


def test_protected_directories_resolve_aliases_and_allow_separate_store(tmp_path: Path) -> None:
    protected = tmp_path / "source"
    protected.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(protected, target_is_directory=True)
    with pytest.raises(ValueError, match="protected"):
        Workbench.open(alias / "workbench", protected_roots=(protected,))
    root = tmp_path / "workbench"
    with Workbench.open(root, protected_roots=(alias, tmp_path / "other")) as workbench:
        workbench.save("a", "A")
    with Workbench.open(root, protected_roots=(protected,)) as workbench:
        assert workbench.contains("a")


def test_protected_roots_requires_a_collection_of_paths(tmp_path: Path) -> None:
    root = tmp_path / "workbench"
    with pytest.raises(TypeError, match="iterable"):
        Workbench.open(root, protected_roots=tmp_path / "source")
    assert not root.exists()
