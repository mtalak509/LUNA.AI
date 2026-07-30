"""WorkspaceManager — user files inside the session zone.

The path cases are listed explicitly (see `_REJECTED_PATHS`) so they cannot quietly drop
out of coverage on the next edit: every one of them is a way a client could try to write
outside the zone, into runtime infrastructure, or into a subagent zone.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.agent.tools.fs_paths import FileAccessError
from core.agent.workspace import (
    MAX_UPLOAD_BYTES,
    FileConflictError,
    FileMissingError,
    FileTooLargeError,
    WorkspaceManager,
)


@pytest.fixture
def zone(tmp_path: Path) -> tuple[WorkspaceManager, Path, Path]:
    """Manager over a fresh session layout: (manager, workspace, checkpoints)."""
    workspace = tmp_path / "session" / "workspace"
    checkpoints = tmp_path / "checkpoints"
    workspace.mkdir(parents=True)
    manager = WorkspaceManager(workspace_path=workspace, checkpoints_root=checkpoints)
    return manager, workspace, checkpoints


# --- writes -----------------------------------------------------------------------


def test_save_writes_file_and_reports_it(zone):
    manager, workspace, _ = zone
    saved = manager.save("report.json", b'{"a": 1}')

    assert (workspace / "report.json").read_bytes() == b'{"a": 1}'
    assert saved.path == "report.json"
    assert saved.size == 8


def test_save_creates_parent_directories(zone):
    manager, workspace, _ = zone
    saved = manager.save("in/deep/nested/report.json", b"{}")

    assert (workspace / "in" / "deep" / "nested" / "report.json").is_file()
    assert saved.path == "in/deep/nested/report.json"


def test_save_takes_a_checkpoint(zone):
    """The write bypasses `@with_checkpoint`, so without this snapshot `/undo` would eat it."""
    manager, _, checkpoints = zone
    saved = manager.save("report.json", b"{}")

    snapshots = sorted(p.name for p in checkpoints.iterdir())
    assert snapshots == [saved.checkpoint]
    assert (checkpoints / saved.checkpoint / "report.json").read_bytes() == b"{}"


def test_save_refuses_to_clobber_by_default(zone):
    manager, workspace, checkpoints = zone
    manager.save("report.json", b"first")

    with pytest.raises(FileConflictError):
        manager.save("report.json", b"second")

    assert (workspace / "report.json").read_bytes() == b"first"
    # A refused write leaves no snapshot behind.
    assert len(list(checkpoints.iterdir())) == 1


def test_save_overwrite_replaces_content_and_snapshots_again(zone):
    manager, workspace, checkpoints = zone
    manager.save("report.json", b"first")
    saved = manager.save("report.json", b"second", overwrite=True)

    assert (workspace / "report.json").read_bytes() == b"second"
    assert len(list(checkpoints.iterdir())) == 2
    assert saved.checkpoint is not None


def test_save_rejects_oversized_content(zone):
    manager, workspace, _ = zone
    with pytest.raises(FileTooLargeError):
        manager.save("big.bin", b"x" * (MAX_UPLOAD_BYTES + 1))

    assert not (workspace / "big.bin").exists()


def test_save_at_the_cap_is_allowed(monkeypatch, zone):
    """The cap is inclusive — only what exceeds it is refused."""
    monkeypatch.setattr("core.agent.workspace.MAX_UPLOAD_BYTES", 4)
    manager, workspace, _ = zone
    manager.save("small.bin", b"xxxx")
    assert (workspace / "small.bin").read_bytes() == b"xxxx"


def test_save_is_idempotent_under_overwrite(zone):
    """Re-running the same upload leaves the same zone content (only snapshots accumulate)."""
    manager, workspace, _ = zone
    first = manager.save("report.json", b'{"a": 1}', overwrite=True)
    second = manager.save("report.json", b'{"a": 1}', overwrite=True)

    assert (first.path, first.size) == (second.path, second.size)
    assert [p.name for p in workspace.rglob("*")] == ["report.json"]


# --- copy_from_host (the CLI `/attach` path) ---------------------------------------


def test_copy_from_host_defaults_to_source_name(tmp_path, zone):
    manager, workspace, checkpoints = zone
    source = tmp_path / "my report.json"
    source.write_bytes(b"{}")

    saved = manager.copy_from_host(source)

    assert saved.path == "my report.json"
    assert (workspace / "my report.json").read_bytes() == b"{}"
    # Same semantics as the HTTP upload: a snapshot is taken here too.
    assert saved.checkpoint in {p.name for p in checkpoints.iterdir()}


def test_copy_from_host_accepts_a_target_path(tmp_path, zone):
    manager, workspace, _ = zone
    source = tmp_path / "data.json"
    source.write_bytes(b"{}")

    saved = manager.copy_from_host(source, "inputs/data.json")

    assert saved.path == "inputs/data.json"
    assert (workspace / "inputs" / "data.json").is_file()


def test_copy_from_host_missing_source(tmp_path, zone):
    manager, _, _ = zone
    with pytest.raises(FileMissingError):
        manager.copy_from_host(tmp_path / "nope.json")


def test_copy_from_host_directory_is_not_a_file(tmp_path, zone):
    manager, _, _ = zone
    (tmp_path / "adir").mkdir()
    with pytest.raises(FileMissingError):
        manager.copy_from_host(tmp_path / "adir")


def test_copy_from_host_checks_size_before_reading(monkeypatch, tmp_path, zone):
    """The cap is checked against `stat`, so an oversized file is never read into memory."""
    monkeypatch.setattr("core.agent.workspace.MAX_UPLOAD_BYTES", 4)
    manager, workspace, _ = zone
    source = tmp_path / "big.bin"
    source.write_bytes(b"xxxxx")

    monkeypatch.setattr(
        Path, "read_bytes", lambda self: pytest.fail("oversized file must not be read")
    )
    with pytest.raises(FileTooLargeError):
        manager.copy_from_host(source)
    assert not (workspace / "big.bin").exists()


# --- delete / read ----------------------------------------------------------------


def test_delete_removes_file_and_snapshots(zone):
    manager, workspace, checkpoints = zone
    manager.save("report.json", b"{}")

    checkpoint = manager.delete("report.json")

    assert not (workspace / "report.json").exists()
    assert checkpoint in {p.name for p in checkpoints.iterdir()}
    # The snapshot records the state *after* the delete — that is what /undo restores from.
    assert not (checkpoints / checkpoint / "report.json").exists()


def test_delete_missing_file(zone):
    manager, _, _ = zone
    with pytest.raises(FileMissingError):
        manager.delete("nope.json")


def test_delete_refuses_a_directory(zone):
    manager, workspace, _ = zone
    (workspace / "adir").mkdir()
    with pytest.raises(FileMissingError):
        manager.delete("adir")
    assert (workspace / "adir").is_dir()


def test_open_for_read_returns_absolute_path(zone):
    manager, workspace, _ = zone
    manager.save("nested/report.json", b"{}")

    target = manager.open_for_read("nested/report.json")

    assert target == workspace / "nested" / "report.json"


def test_open_for_read_missing_file(zone):
    manager, _, _ = zone
    with pytest.raises(FileMissingError):
        manager.open_for_read("nope.json")


# --- listing ----------------------------------------------------------------------


def test_list_is_flat_sorted_and_hides_runtime(zone):
    manager, workspace, _ = zone
    manager.save("b.json", b"22")
    manager.save("a/c.json", b"333")
    runtime = workspace / ".runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / "messages.jsonl").write_bytes(b"{}")

    listed = manager.list()

    assert [f.path for f in listed] == ["a/c.json", "b.json"]
    assert [f.size for f in listed] == [3, 2]
    assert all(f.mtime.tzinfo is not None for f in listed)


def test_list_empty_and_missing_zone(zone, tmp_path):
    manager, _, _ = zone
    assert manager.list() == []

    absent = WorkspaceManager(
        workspace_path=tmp_path / "gone" / "workspace", checkpoints_root=tmp_path / "cp"
    )
    assert absent.list() == []


# --- path policy ------------------------------------------------------------------

# Every way a client could aim outside the zone. Kept as one explicit list: the HTTP and
# CLI surfaces share this resolver, so a gap here is a gap in both.
_REJECTED_PATHS = [
    "../escape.json",
    "a/../../escape.json",
    r"a\..\..\escape.json",  # Windows separators must not slip past the posix logic
    "/etc/passwd",
    "C:/abs.json",
    "C:abs.json",
    r"C:\abs.json",
    "D:/other-drive.json",
    r"\\server\share\f.json",
    ".runtime/messages.jsonl",
    "a/../.runtime/messages.jsonl",
    "subagents/uid/result.json",  # granted to the main agent for promotion, not for uploads
    ".hidden",
    "dir/.hidden",
    "",
    "   ",
]


@pytest.mark.parametrize("rel", _REJECTED_PATHS)
def test_resolve_rejects(zone, rel):
    manager, _, _ = zone
    with pytest.raises(FileAccessError):
        manager.resolve(rel)


@pytest.mark.parametrize("rel", _REJECTED_PATHS)
def test_save_rejects_the_same_paths(zone, rel):
    """The check sits in `resolve`, so every write inherits it — nothing is written."""
    manager, workspace, checkpoints = zone
    with pytest.raises(FileAccessError):
        manager.save(rel, b"payload")
    assert list(workspace.rglob("*")) == []
    assert not checkpoints.exists()


def test_resolve_normalizes_windows_separators(zone):
    manager, workspace, _ = zone
    assert manager.resolve(r"inputs\report.json") == workspace / "inputs" / "report.json"


def test_resolve_stays_inside_a_symlinked_zone(zone):
    """`PathScope` resolves symlinks, so the confinement check compares real paths."""
    manager, workspace, _ = zone
    assert manager.resolve("report.json").parent == workspace.resolve()


# --- checkpoint resilience --------------------------------------------------------


def test_write_survives_a_failing_snapshot(monkeypatch, zone):
    """The file is already on disk: a broken snapshot must not report the write as failed."""
    manager, workspace, _ = zone

    def boom(self, label):
        raise OSError("no space left")

    monkeypatch.setattr("core.agent.checkpoint.CheckpointManager.snapshot", boom)
    saved = manager.save("report.json", b"{}")

    assert saved.checkpoint is None
    assert (workspace / "report.json").read_bytes() == b"{}"
