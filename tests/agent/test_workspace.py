"""WorkspaceManager — the user's file operations on a session's zone.

Two layers to cover: the zone layer (anywhere inside `workspace/`) and the attachments facet
pinned to `workspace/attachments/`, which is what the HTTP and CLI surfaces expose. Most tests
here drive the facet, since that is the path in production; the zone layer gets its own section.

The path cases are listed explicitly (`_REJECTED_PATHS` for the facet, `_REJECTED_ZONE_PATHS`
for the zone) so they cannot quietly drop out of coverage on the next edit: every one of them
is a way a client could try to write where it must not.
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
    list_attachments,
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


def test_save_lands_in_the_attachments_directory(zone):
    """One directory for user files is what makes the context block a plain listing."""
    manager, workspace, _ = zone
    saved = manager.attach("report.json", b'{"a": 1}')

    assert (workspace / "attachments" / "report.json").read_bytes() == b'{"a": 1}'
    assert saved.path == "report.json"  # reported relative to attachments/
    assert saved.size == 8


def test_save_creates_parent_directories(zone):
    manager, workspace, _ = zone
    saved = manager.attach("in/deep/nested/report.json", b"{}")

    assert (workspace / "attachments" / "in" / "deep" / "nested" / "report.json").is_file()
    assert saved.path == "in/deep/nested/report.json"


def test_save_takes_a_checkpoint(zone):
    """The write bypasses `@with_checkpoint`, so without this snapshot `/undo` would eat it."""
    manager, _, checkpoints = zone
    saved = manager.attach("report.json", b"{}")

    snapshots = sorted(p.name for p in checkpoints.iterdir())
    assert snapshots == [saved.checkpoint]
    assert (checkpoints / saved.checkpoint / "attachments" / "report.json").read_bytes() == b"{}"


def test_save_refuses_to_clobber_by_default(zone):
    manager, _, checkpoints = zone
    manager.attach("report.json", b"first")

    with pytest.raises(FileConflictError):
        manager.attach("report.json", b"second")

    assert manager.open_attachment("report.json").read_bytes() == b"first"
    # A refused write leaves no snapshot behind.
    assert len(list(checkpoints.iterdir())) == 1


def test_save_overwrite_replaces_content_and_snapshots_again(zone):
    manager, _, checkpoints = zone
    manager.attach("report.json", b"first")
    saved = manager.attach("report.json", b"second", overwrite=True)

    assert manager.open_attachment("report.json").read_bytes() == b"second"
    assert len(list(checkpoints.iterdir())) == 2
    assert saved.checkpoint is not None


def test_save_rejects_oversized_content(zone):
    manager, workspace, _ = zone
    with pytest.raises(FileTooLargeError):
        manager.attach("big.bin", b"x" * (MAX_UPLOAD_BYTES + 1))

    assert not (workspace / "attachments").exists()


def test_save_at_the_cap_is_allowed(monkeypatch, zone):
    """The cap is inclusive — only what exceeds it is refused."""
    monkeypatch.setattr("core.agent.workspace.MAX_UPLOAD_BYTES", 4)
    manager, _, _ = zone
    manager.attach("small.bin", b"xxxx")
    assert manager.open_attachment("small.bin").read_bytes() == b"xxxx"


def test_save_is_idempotent_under_overwrite(zone):
    """Re-running the same upload leaves the same content (only snapshots accumulate)."""
    manager, _, _ = zone
    first = manager.attach("report.json", b'{"a": 1}', overwrite=True)
    second = manager.attach("report.json", b'{"a": 1}', overwrite=True)

    assert (first.path, first.size) == (second.path, second.size)
    assert [f.path for f in manager.list_attachments()] == ["report.json"]


# --- copy_from_host (the CLI `/attach` path) ---------------------------------------


def test_copy_from_host_defaults_to_source_name(tmp_path, zone):
    manager, workspace, checkpoints = zone
    source = tmp_path / "my report.json"
    source.write_bytes(b"{}")

    saved = manager.attach_from_host(source)

    assert saved.path == "my report.json"
    assert (workspace / "attachments" / "my report.json").read_bytes() == b"{}"
    # Same semantics as the HTTP upload: a snapshot is taken here too.
    assert saved.checkpoint in {p.name for p in checkpoints.iterdir()}


def test_copy_from_host_accepts_a_target_path(tmp_path, zone):
    manager, workspace, _ = zone
    source = tmp_path / "data.json"
    source.write_bytes(b"{}")

    saved = manager.attach_from_host(source, "inputs/data.json")

    assert saved.path == "inputs/data.json"
    assert (workspace / "attachments" / "inputs" / "data.json").is_file()


def test_copy_from_host_missing_source(tmp_path, zone):
    manager, _, _ = zone
    with pytest.raises(FileMissingError):
        manager.attach_from_host(tmp_path / "nope.json")


def test_copy_from_host_directory_is_not_a_file(tmp_path, zone):
    manager, _, _ = zone
    (tmp_path / "adir").mkdir()
    with pytest.raises(FileMissingError):
        manager.attach_from_host(tmp_path / "adir")


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
        manager.attach_from_host(source)
    assert not (workspace / "attachments").exists()


# --- delete / read ----------------------------------------------------------------


def test_delete_removes_file_and_snapshots(zone):
    manager, workspace, checkpoints = zone
    manager.attach("report.json", b"{}")

    checkpoint = manager.delete_attachment("report.json")

    assert not (workspace / "attachments" / "report.json").exists()
    assert checkpoint in {p.name for p in checkpoints.iterdir()}
    # The snapshot records the state *after* the delete — that is what /undo restores from.
    assert not (checkpoints / checkpoint / "attachments" / "report.json").exists()


def test_delete_drops_the_file_out_of_the_listing(zone):
    """No bookkeeping to correct: the listing is the state, so a delete is simply gone."""
    manager, _, _ = zone
    manager.attach("report.json", b"{}")
    manager.delete_attachment("report.json")

    assert manager.list_attachments() == []


def test_delete_missing_file(zone):
    manager, _, _ = zone
    with pytest.raises(FileMissingError):
        manager.delete_attachment("nope.json")


def test_delete_refuses_a_directory(zone):
    manager, workspace, _ = zone
    (workspace / "attachments" / "adir").mkdir(parents=True)
    with pytest.raises(FileMissingError):
        manager.delete_attachment("adir")
    assert (workspace / "attachments" / "adir").is_dir()


def test_open_for_read_returns_absolute_path(zone):
    manager, workspace, _ = zone
    manager.attach("nested/report.json", b"{}")

    target = manager.open_attachment("nested/report.json")

    assert target == workspace / "attachments" / "nested" / "report.json"


def test_open_for_read_missing_file(zone):
    manager, _, _ = zone
    with pytest.raises(FileMissingError):
        manager.open_attachment("nope.json")


# --- listing ----------------------------------------------------------------------


def test_list_is_flat_sorted_and_relative_to_attachments(zone):
    manager, _, _ = zone
    manager.attach("b.json", b"22")
    manager.attach("a/c.json", b"333")

    listed = manager.list_attachments()

    assert [f.path for f in listed] == ["a/c.json", "b.json"]
    assert [f.size for f in listed] == [3, 2]
    assert all(f.mtime.tzinfo is not None for f in listed)


def test_list_ignores_the_rest_of_the_workspace(zone):
    """Only the dedicated directory is listed — the agent's own files are not the user's."""
    manager, workspace, _ = zone
    (workspace / "artifacts").mkdir()
    (workspace / "artifacts" / "agent-made.json").write_bytes(b"{}")
    (workspace / "top-level.json").write_bytes(b"{}")
    manager.attach("attached.json", b"{}")

    assert [f.path for f in manager.list_attachments()] == ["attached.json"]


def test_list_empty_and_missing_directory(zone, tmp_path):
    manager, workspace, _ = zone
    assert manager.list_attachments() == []  # attachments/ not created yet

    (workspace / "attachments").mkdir()
    assert manager.list_attachments() == []  # created but empty

    assert list_attachments(tmp_path / "gone" / "workspace") == []


# --- path policy ------------------------------------------------------------------

# Every way a client could aim outside `attachments/`. Kept as one explicit list: the HTTP
# and CLI surfaces share this resolver, so a gap here is a gap in both.
_REJECTED_PATHS = [
    "../escape.json",  # out of attachments/, into the agent's part of the zone
    "../../escape.json",  # out of the zone entirely
    "a/../../escape.json",
    r"a\..\..\escape.json",  # Windows separators must not slip past the posix logic
    "../artifacts/report.json",
    "../.runtime/messages.jsonl",
    "/etc/passwd",
    "C:/abs.json",
    "C:abs.json",
    r"C:\abs.json",
    "D:/other-drive.json",
    r"\\server\share\f.json",
    ".hidden",
    "dir/.hidden",
    "",
    "   ",
]


@pytest.mark.parametrize("rel", _REJECTED_PATHS)
def test_resolve_rejects(zone, rel):
    manager, _, _ = zone
    with pytest.raises(FileAccessError):
        manager.resolve_attachment(rel)


@pytest.mark.parametrize("rel", _REJECTED_PATHS)
def test_save_rejects_the_same_paths(zone, rel):
    """The check sits in `resolve`, so every write inherits it — nothing is written."""
    manager, workspace, checkpoints = zone
    with pytest.raises(FileAccessError):
        manager.attach(rel, b"payload")
    assert list(workspace.rglob("*")) == []
    assert not checkpoints.exists()


def test_resolve_normalizes_windows_separators(zone):
    manager, workspace, _ = zone
    assert (
        manager.resolve_attachment(r"inputs\report.json")
        == workspace / "attachments" / "inputs" / "report.json"
    )


def test_resolve_stays_inside_the_attachments_root(zone):
    manager, _, _ = zone
    assert manager.resolve_attachment("report.json").parent == manager.attachments_root


# --- the zone layer ---------------------------------------------------------------
#
# The general operations the attachments facet is built on: they reach anywhere inside the
# zone, so they carry the same guarantees minus the attachments confinement.


def test_zone_save_writes_anywhere_in_the_zone(zone):
    manager, workspace, checkpoints = zone
    saved = manager.save("data/report.json", b'{"a": 1}')

    assert (workspace / "data" / "report.json").read_bytes() == b'{"a": 1}'
    assert saved.path == "data/report.json"  # reported relative to the zone root
    assert saved.checkpoint in {p.name for p in checkpoints.iterdir()}


def test_zone_save_respects_conflicts_and_the_cap(monkeypatch, zone):
    manager, _, _ = zone
    manager.save("report.json", b"first")
    with pytest.raises(FileConflictError):
        manager.save("report.json", b"second")

    monkeypatch.setattr("core.agent.workspace.MAX_UPLOAD_BYTES", 4)
    with pytest.raises(FileTooLargeError):
        manager.save("big.bin", b"xxxxx")


def test_zone_read_delete_and_list(zone):
    manager, workspace, _ = zone
    manager.save("report.json", b"{}")
    manager.attach("attached.json", b"{}")

    assert manager.open_for_read("report.json") == workspace / "report.json"
    # The zone listing sees everything the user put there, attachments included.
    assert [f.path for f in manager.list()] == ["attachments/attached.json", "report.json"]

    manager.delete("report.json")
    assert [f.path for f in manager.list()] == ["attachments/attached.json"]
    with pytest.raises(FileMissingError):
        manager.delete("report.json")


def test_zone_listing_hides_runtime(zone):
    """`.runtime/` holds the history and belongs to no one outside the runtime."""
    manager, workspace, _ = zone
    (workspace / ".runtime").mkdir()
    (workspace / ".runtime" / "messages.jsonl").write_bytes(b"{}")
    manager.save("report.json", b"{}")

    assert [f.path for f in manager.list()] == ["report.json"]


# Ways out of the zone itself. Shorter than the attachments list on purpose: `..` into the
# agent's own directories is legitimate here, so only real escapes remain.
_REJECTED_ZONE_PATHS = [
    "../escape.json",
    "a/../../escape.json",
    r"a\..\..\escape.json",
    "/etc/passwd",
    "C:/abs.json",
    r"\\server\share\f.json",
    ".runtime/messages.jsonl",  # blocked by PathScope for tools, and here too
    "a/../.runtime/messages.jsonl",
    "subagents/uid/result.json",  # granted to the main agent for promotion, not for uploads
    ".hidden",
    "",
]


@pytest.mark.parametrize("rel", _REJECTED_ZONE_PATHS)
def test_zone_resolve_rejects(zone, rel):
    manager, workspace, _ = zone
    with pytest.raises(FileAccessError):
        manager.resolve(rel)
    with pytest.raises(FileAccessError):
        manager.save(rel, b"payload")
    assert list(workspace.rglob("*")) == []


def test_zone_resolve_allows_the_agents_own_directories(zone):
    """Nothing is off-limits inside the zone — that is what makes this the general layer."""
    manager, workspace, _ = zone
    assert manager.resolve("artifacts/out.json") == workspace / "artifacts" / "out.json"
    assert manager.resolve("notes/decisions.md") == workspace / "notes" / "decisions.md"


# --- checkpoint resilience --------------------------------------------------------


def test_write_survives_a_failing_snapshot(monkeypatch, zone):
    """The file is already on disk: a broken snapshot must not report the write as failed."""
    manager, _, _ = zone

    def boom(self, label):
        raise OSError("no space left")

    monkeypatch.setattr("core.agent.checkpoint.CheckpointManager.snapshot", boom)
    saved = manager.attach("report.json", b"{}")

    assert saved.checkpoint is None
    assert manager.open_attachment("report.json").read_bytes() == b"{}"
