"""
Unit-тесты шага 3.1: PathScope — резолвер путей файловых тулов + изоляция зон.

Без сети и LLM. Зоны — реальные temp-каталоги (tmp_path); проверяем, что относительный
путь резолвится в свою зону и что любой побег (traversal/symlink/абсолют/.runtime) и
нарушение RO-вида отбиваются доменной FileAccessError.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.agent.tools.fs_paths import FileAccessError, PathScope, _safe_join


@pytest.fixture()
def zones(tmp_path: Path) -> tuple[Path, Path]:
    """Готовые зоны на диске: (workspace, subagent)."""
    workspace = tmp_path / "session" / "workspace"
    subagent = tmp_path / "session" / "subagents" / "abc123"
    (workspace / "notes").mkdir(parents=True)
    (workspace / ".runtime").mkdir()
    (subagent / "notes").mkdir(parents=True)
    (subagent / ".runtime").mkdir()
    return workspace, subagent


# --- _safe_join: ядро безопасности ---------------------------------------------------


def test_safe_join_normal_path(zones: tuple[Path, Path]) -> None:
    workspace, _ = zones
    assert _safe_join(workspace, "notes/a.md") == (workspace / "notes" / "a.md").resolve()


def test_safe_join_dot_is_zone_root(zones: tuple[Path, Path]) -> None:
    workspace, _ = zones
    assert _safe_join(workspace, ".") == workspace.resolve()


@pytest.mark.parametrize("bad", ["", "   ", "\t"])
def test_safe_join_empty_rejected(zones: tuple[Path, Path], bad: str) -> None:
    workspace, _ = zones
    with pytest.raises(FileAccessError):
        _safe_join(workspace, bad)


@pytest.mark.parametrize("bad", ["/etc/passwd", "/", "/session/workspace/notes/a.md"])
def test_safe_join_absolute_rejected(zones: tuple[Path, Path], bad: str) -> None:
    workspace, _ = zones
    with pytest.raises(FileAccessError):
        _safe_join(workspace, bad)


@pytest.mark.parametrize("bad", ["../outside.txt", "notes/../../escape.txt", "../../.."])
def test_safe_join_traversal_rejected(zones: tuple[Path, Path], bad: str) -> None:
    workspace, _ = zones
    with pytest.raises(FileAccessError):
        _safe_join(workspace, bad)


def test_safe_join_runtime_blocked_direct(zones: tuple[Path, Path]) -> None:
    workspace, _ = zones
    with pytest.raises(FileAccessError):
        _safe_join(workspace, ".runtime/messages.jsonl")


def test_safe_join_runtime_blocked_via_traversal(zones: tuple[Path, Path]) -> None:
    """Хитрый кейс: первый сегмент не .runtime, но после нормализации путь ведёт в .runtime."""
    workspace, _ = zones
    with pytest.raises(FileAccessError):
        _safe_join(workspace, "notes/../.runtime/messages.jsonl")


def test_safe_join_nested_dot_runtime_allowed(zones: tuple[Path, Path]) -> None:
    """Блокируется только .runtime В КОРНЕ зоны; одноимённая вложенная папка — обычный файл."""
    workspace, _ = zones
    target = _safe_join(workspace, "notes/.runtime/x.md")
    assert target == (workspace / "notes" / ".runtime" / "x.md").resolve()


def test_safe_join_symlink_escape_rejected(zones: tuple[Path, Path], tmp_path: Path) -> None:
    """Симлинк, ведущий за пределы зоны, отбивается (resolve() следует по ссылке)."""
    workspace, _ = zones
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "leak.txt").write_text("x", encoding="utf-8")
    try:
        os.symlink(secret, workspace / "link")
    except (OSError, NotImplementedError):
        pytest.skip("symlink недоступен (нет привилегий / ОС)")
    with pytest.raises(FileAccessError):
        _safe_join(workspace, "link/leak.txt")


# --- PathScope: конструктор ----------------------------------------------------------


def test_scope_rejects_unknown_agent_scope(zones: tuple[Path, Path]) -> None:
    workspace, _ = zones
    with pytest.raises(ValueError):
        PathScope(agent_scope="root", workspace_path=workspace)


def test_scope_subagent_requires_subagent_path(zones: tuple[Path, Path]) -> None:
    workspace, _ = zones
    with pytest.raises(ValueError):
        PathScope(agent_scope="subagent", workspace_path=workspace)


# --- PathScope: MainAgent ------------------------------------------------------------


def test_main_write_and_read_resolve_to_workspace(zones: tuple[Path, Path]) -> None:
    workspace, _ = zones
    scope = PathScope(agent_scope="main", workspace_path=workspace)
    expected = (workspace / "notes" / "a.md").resolve()
    assert scope.resolve_write("notes/a.md") == expected
    assert scope.resolve_read("notes/a.md") == expected


def test_main_runtime_blocked_both_directions(zones: tuple[Path, Path]) -> None:
    workspace, _ = zones
    scope = PathScope(agent_scope="main", workspace_path=workspace)
    with pytest.raises(FileAccessError):
        scope.resolve_write(".runtime/messages.jsonl")
    with pytest.raises(FileAccessError):
        scope.resolve_read(".runtime/messages.jsonl")


# --- PathScope: MainAgent ↔ зоны субагентов (промоут/cleanup, v2-storage §3.6) -------


def test_main_reads_subagent_artifact_via_prefix(zones: tuple[Path, Path]) -> None:
    """Баг-сценарий промоута: MainAgent читает artifacts субагента через `subagents/`."""
    workspace, subagent = zones
    scope = PathScope(agent_scope="main", workspace_path=workspace)
    # subagent зона — /session/subagents/abc123, сосед workspace
    expected = (subagent / "artifacts" / "count.txt").resolve()
    assert scope.resolve_read("subagents/abc123/artifacts/count.txt") == expected


def test_main_writes_subagent_zone_via_prefix_for_cleanup(zones: tuple[Path, Path]) -> None:
    """RW по §3.6: MainAgent может писать/удалять в зоне субагента (cleanup)."""
    workspace, subagent = zones
    scope = PathScope(agent_scope="main", workspace_path=workspace)
    expected = (subagent / "artifacts" / "count.txt").resolve()
    assert scope.resolve_write("subagents/abc123/artifacts/count.txt") == expected


def test_main_subagents_bare_prefix_is_subagents_root(zones: tuple[Path, Path]) -> None:
    workspace, _ = zones
    scope = PathScope(agent_scope="main", workspace_path=workspace)
    assert scope.resolve_read("subagents") == (workspace.parent / "subagents").resolve()


def test_main_subagents_prefix_escape_back_to_workspace_rejected(zones: tuple[Path, Path]) -> None:
    """`..`-побег из subagents/ обратно в workspace/.runtime блокируется (выход за корень)."""
    workspace, _ = zones
    scope = PathScope(agent_scope="main", workspace_path=workspace)
    with pytest.raises(FileAccessError):
        scope.resolve_read("subagents/../workspace/.runtime/messages.jsonl")


def test_subagent_has_no_subagents_prefix_privilege(zones: tuple[Path, Path]) -> None:
    """`subagents/` — привилегия только MainAgent; у субагента префикс не особый,
    резолвится буквально в его собственной зоне (изоляция цела)."""
    workspace, subagent = zones
    scope = PathScope(agent_scope="subagent", workspace_path=workspace, subagent_path=subagent)
    assert scope.resolve_read("subagents/other/x.txt") == (
        subagent / "subagents" / "other" / "x.txt"
    ).resolve()


# --- PathScope: субагент -------------------------------------------------------------


def test_subagent_paths_resolve_to_own_zone(zones: tuple[Path, Path]) -> None:
    workspace, subagent = zones
    scope = PathScope(agent_scope="subagent", workspace_path=workspace, subagent_path=subagent)
    expected = (subagent / "notes" / "working.md").resolve()
    assert scope.resolve_write("notes/working.md") == expected
    assert scope.resolve_read("notes/working.md") == expected


def test_subagent_reads_workspace_via_prefix(zones: tuple[Path, Path]) -> None:
    workspace, subagent = zones
    scope = PathScope(agent_scope="subagent", workspace_path=workspace, subagent_path=subagent)
    assert scope.resolve_read("workspace/notes/decisions.md") == (
        workspace / "notes" / "decisions.md"
    ).resolve()


def test_subagent_read_bare_prefix_is_workspace_root(zones: tuple[Path, Path]) -> None:
    workspace, subagent = zones
    scope = PathScope(agent_scope="subagent", workspace_path=workspace, subagent_path=subagent)
    assert scope.resolve_read("workspace") == workspace.resolve()


def test_subagent_write_to_workspace_prefix_rejected(zones: tuple[Path, Path]) -> None:
    """RO-вид: субагент не может писать в общий workspace через префикс."""
    workspace, subagent = zones
    scope = PathScope(agent_scope="subagent", workspace_path=workspace, subagent_path=subagent)
    with pytest.raises(FileAccessError):
        scope.resolve_write("workspace/notes/decisions.md")


def test_subagent_runtime_blocked_in_own_zone(zones: tuple[Path, Path]) -> None:
    workspace, subagent = zones
    scope = PathScope(agent_scope="subagent", workspace_path=workspace, subagent_path=subagent)
    with pytest.raises(FileAccessError):
        scope.resolve_read(".runtime/messages.jsonl")


def test_subagent_runtime_blocked_through_workspace_prefix(zones: tuple[Path, Path]) -> None:
    """RO-вид workspace тоже не вскрывает .runtime — _safe_join режет его и в workspace."""
    workspace, subagent = zones
    scope = PathScope(agent_scope="subagent", workspace_path=workspace, subagent_path=subagent)
    with pytest.raises(FileAccessError):
        scope.resolve_read("workspace/.runtime/messages.jsonl")
