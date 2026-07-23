"""
Unit-тесты шага 3.4: шесть файловых тулов (read/write/edit/list/search/delete).

Тулы гоняются через `.ainvoke({...})` — реальный путь вызова. Рантайм-контекст (get_runtime)
подменяется monkeypatch'ем в ДВУХ модулях: files_tools (PathScope) и checkpoint (снимок).
Граф create_agent не поднимаем.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent import checkpoint as cp
from core.agent.checkpoint import CheckpointManager
from core.agent.tools import files_tools as ft
from core.agent.tools.fs_paths import FileAccessError


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path, Path]:
    """(workspace, checkpoints, subagent_zone). Зоны заполнены notes/ + .runtime/."""
    workspace = tmp_path / "session" / "workspace"
    checkpoints = tmp_path / "checkpoints"
    subagent = tmp_path / "session" / "subagents" / "uid"
    (workspace / "notes").mkdir(parents=True)
    (workspace / ".runtime").mkdir()
    (subagent / "notes").mkdir(parents=True)
    (subagent / ".runtime").mkdir()
    return workspace, checkpoints, subagent


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    workspace: Path,
    checkpoints: Path,
    agent_scope: str = "main",
    subagent: Path | None = None,
) -> None:
    ctx = SimpleNamespace(
        agent_scope=agent_scope,
        workspace_path=workspace,
        checkpoints_root=checkpoints,
        subagent_path=subagent,
    )
    rt = SimpleNamespace(context=ctx)
    monkeypatch.setattr(ft, "get_runtime", lambda *a, **k: rt)
    monkeypatch.setattr(cp, "get_runtime", lambda *a, **k: rt)


def _checkpoints(workspace: Path, checkpoints: Path) -> list[str]:
    return [c.id for c in CheckpointManager(
        workspace_path=workspace, checkpoints_root=checkpoints
    ).list()]


# --- round-trip + чекпоинт -----------------------------------------------------------


async def test_write_then_read_roundtrip(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)

    await ft.write_file.ainvoke({"path": "notes/a.md", "content": "hello"})
    assert (workspace / "notes" / "a.md").read_text(encoding="utf-8") == "hello"
    assert await ft.read_file.ainvoke({"path": "notes/a.md"}) == "hello"


async def test_write_triggers_checkpoint(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    await ft.write_file.ainvoke({"path": "x.md", "content": "v"})
    assert _checkpoints(workspace, checkpoints) == ["c000_write_file"]


async def test_read_missing_raises(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(FileAccessError):
        await ft.read_file.ainvoke({"path": "nope.md"})


# --- скоуп / .runtime / traversal ----------------------------------------------------


async def test_write_to_runtime_blocked_and_no_checkpoint(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(FileAccessError):
        await ft.write_file.ainvoke({"path": ".runtime/hack.jsonl", "content": "x"})
    # отказ резолвера до тела → снимка нет (failed tool call чекпоинт не создаёт)
    assert not checkpoints.exists()


async def test_write_traversal_blocked(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(FileAccessError):
        await ft.write_file.ainvoke({"path": "../escape.md", "content": "x"})


# --- list_files ----------------------------------------------------------------------


async def test_list_hides_runtime_and_sorts(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    (workspace / "z.md").write_text("z", encoding="utf-8")
    out = await ft.list_files.ainvoke({"path": "."})
    lines = out.splitlines()
    assert ".runtime" not in out          # runtime-каталог скрыт
    assert lines == ["notes/", "z.md"]    # каталоги раньше файлов


async def test_list_empty_dir(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    assert await ft.list_files.ainvoke({"path": "notes"}) == "(пусто)"


# --- search_files --------------------------------------------------------------------


async def test_search_by_name_and_content(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    (workspace / "alpha.md").write_text("nothing here", encoding="utf-8")
    (workspace / "notes" / "b.md").write_text("line1\nsecret needle here\nline3", encoding="utf-8")

    by_name = await ft.search_files.ainvoke({"query": "alpha"})
    assert "alpha.md" in by_name

    by_content = await ft.search_files.ainvoke({"query": "needle"})
    assert "notes/b.md:2:" in by_content.replace("\\", "/")


async def test_search_empty_query_raises(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(FileAccessError):
        await ft.search_files.ainvoke({"query": "   "})


# --- edit_file -----------------------------------------------------------------------


async def test_edit_single_replace(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    (workspace / "c.md").write_text("foo bar baz", encoding="utf-8")
    await ft.edit_file.ainvoke({"path": "c.md", "old_string": "bar", "new_string": "QUX"})
    assert (workspace / "c.md").read_text(encoding="utf-8") == "foo QUX baz"


async def test_edit_ambiguous_raises_and_no_write(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    (workspace / "d.md").write_text("x x x", encoding="utf-8")
    with pytest.raises(FileAccessError):
        await ft.edit_file.ainvoke({"path": "d.md", "old_string": "x", "new_string": "Y"})
    assert (workspace / "d.md").read_text(encoding="utf-8") == "x x x"  # файл не тронут
    assert not checkpoints.exists()  # ошибка до записи → снимка нет


async def test_edit_replace_all(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    (workspace / "e.md").write_text("x x x", encoding="utf-8")
    await ft.edit_file.ainvoke(
        {"path": "e.md", "old_string": "x", "new_string": "Y", "replace_all": True}
    )
    assert (workspace / "e.md").read_text(encoding="utf-8") == "Y Y Y"


async def test_edit_missing_file_raises(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(FileAccessError):
        await ft.edit_file.ainvoke({"path": "ghost.md", "old_string": "a", "new_string": "b"})


# --- delete_file ---------------------------------------------------------------------


async def test_delete_file(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    (workspace / "g.md").write_text("g", encoding="utf-8")
    await ft.delete_file.ainvoke({"path": "g.md"})
    assert not (workspace / "g.md").exists()
    assert _checkpoints(workspace, checkpoints) == ["c000_delete_file"]


async def test_delete_missing_raises(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(FileAccessError):
        await ft.delete_file.ainvoke({"path": "ghost.md"})


# --- субагентский скоуп --------------------------------------------------------------


async def test_subagent_writes_to_own_zone(env, monkeypatch) -> None:
    workspace, checkpoints, subagent = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints,
           agent_scope="subagent", subagent=subagent)
    await ft.write_file.ainvoke({"path": "notes/working.md", "content": "sub"})
    assert (subagent / "notes" / "working.md").read_text(encoding="utf-8") == "sub"
    assert not (workspace / "notes" / "working.md").exists()  # в workspace не попало


async def test_subagent_cannot_write_workspace(env, monkeypatch) -> None:
    workspace, checkpoints, subagent = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints,
           agent_scope="subagent", subagent=subagent)
    with pytest.raises(FileAccessError):
        await ft.write_file.ainvoke({"path": "workspace/notes/x.md", "content": "x"})


async def test_subagent_reads_workspace_readonly(env, monkeypatch) -> None:
    workspace, checkpoints, subagent = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints,
           agent_scope="subagent", subagent=subagent)
    (workspace / "notes" / "decisions.md").write_text("RO", encoding="utf-8")
    out = await ft.read_file.ainvoke({"path": "workspace/notes/decisions.md"})
    assert out == "RO"
