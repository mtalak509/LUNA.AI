"""
Unit tests for the file tools: read, write, edit, ls, find, grep, rm.

Tools are driven through `.ainvoke({...})`, the real call path. The runtime context
(`get_runtime`) is monkeypatched in two modules: files_tools (PathScope) and checkpoint
(snapshots). The create_agent graph is not built.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent import checkpoint as cp
from core.agent.checkpoint import CheckpointManager
from core.agent.tools import files_tools as ft
from core.agent.tools import tool_output as to
from core.agent.tools.fs_paths import SPILL_DIR, FileAccessError


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path, Path]:
    """(workspace, checkpoints, subagent_zone). Zones are filled with notes/ + .runtime/."""
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
    monkeypatch.setattr(to, "get_runtime", lambda *a, **k: rt)


def _checkpoints(workspace: Path, checkpoints: Path) -> list[str]:
    return [
        c.id
        for c in CheckpointManager(workspace_path=workspace, checkpoints_root=checkpoints).list()
    ]


# --- round-trip + чекпоинт -----------------------------------------------------------


async def test_write_then_read_roundtrip(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)

    await ft.write.ainvoke({"path": "notes/a.md", "content": "hello"})
    assert (workspace / "notes" / "a.md").read_text(encoding="utf-8") == "hello"
    assert await ft.read.ainvoke({"path": "notes/a.md"}) == "hello"


async def test_write_triggers_checkpoint(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    await ft.write.ainvoke({"path": "x.md", "content": "v"})
    assert _checkpoints(workspace, checkpoints) == ["c000_write"]


async def test_read_missing_raises(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(FileAccessError):
        await ft.read.ainvoke({"path": "nope.md"})


# --- скоуп / .runtime / traversal ----------------------------------------------------


async def test_write_to_runtime_blocked_and_no_checkpoint(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(FileAccessError):
        await ft.write.ainvoke({"path": ".runtime/hack.jsonl", "content": "x"})
    # отказ резолвера до тела → снимка нет (failed tool call чекпоинт не создаёт)
    assert not checkpoints.exists()


async def test_write_traversal_blocked(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(FileAccessError):
        await ft.write.ainvoke({"path": "../escape.md", "content": "x"})


# --- ls ------------------------------------------------------------------------------


async def test_list_hides_runtime_and_sorts(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    (workspace / "z.md").write_text("z", encoding="utf-8")
    out = await ft.ls.ainvoke({"path": "."})
    lines = out.splitlines()
    assert ".runtime" not in out  # runtime-каталог скрыт
    assert lines == ["notes/", "z.md"]  # каталоги раньше файлов


async def test_list_empty_dir(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    assert await ft.ls.ainvoke({"path": "notes"}) == "(пусто)"


# --- read window ---------------------------------------------------------------------


def _big(workspace: Path) -> None:
    (workspace / "notes" / "big.md").write_text(
        "".join(f"line{i}\n" for i in range(1, 11)), encoding="utf-8"
    )


async def test_read_whole_file_without_window(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _big(workspace)
    out = await ft.read.ainvoke({"path": "notes/big.md"})
    assert out == "".join(f"line{i}\n" for i in range(1, 11))


async def test_read_window_middle(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _big(workspace)
    out = await ft.read.ainvoke({"path": "notes/big.md", "offset": 4, "limit": 3})
    assert out == "[строки 4–6 из 10]\nline4\nline5\nline6\n"


async def test_read_window_to_end(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _big(workspace)
    out = await ft.read.ainvoke({"path": "notes/big.md", "offset": 9})
    assert out == "[строки 9–10 из 10]\nline9\nline10\n"


async def test_read_limit_only_starts_at_first_line(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _big(workspace)
    out = await ft.read.ainvoke({"path": "notes/big.md", "limit": 2})
    assert out == "[строки 1–2 из 10]\nline1\nline2\n"


async def test_read_limit_past_end_clamped(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _big(workspace)
    out = await ft.read.ainvoke({"path": "notes/big.md", "offset": 9, "limit": 50})
    assert out.startswith("[строки 9–10 из 10]\n")


async def test_read_offset_past_end_raises(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _big(workspace)
    with pytest.raises(FileAccessError, match="10"):
        await ft.read.ainvoke({"path": "notes/big.md", "offset": 11})


@pytest.mark.parametrize("window", [{"offset": 0}, {"limit": 0}, {"offset": -1, "limit": 2}])
async def test_read_invalid_window_raises(env, monkeypatch, window) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _big(workspace)
    with pytest.raises(FileAccessError):
        await ft.read.ainvoke({"path": "notes/big.md", **window})


async def test_read_window_normalizes_crlf_and_keeps_last_line(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    (workspace / "crlf.md").write_bytes(b"a\r\nb\r\nc")
    out = await ft.read.ainvoke({"path": "crlf.md", "offset": 2})
    assert out == "[строки 2–3 из 3]\nb\nc"  # the same newlines edit sees


# --- find ----------------------------------------------------------------------------


def _tree(workspace: Path) -> None:
    (workspace / "notes" / "sub").mkdir()
    (workspace / "notes" / "a.md").write_text("a", encoding="utf-8")
    (workspace / "notes" / "b.txt").write_text("b", encoding="utf-8")
    (workspace / "notes" / "sub" / "c.md").write_text("c", encoding="utf-8")
    (workspace / ".runtime" / "x.md").write_text("x", encoding="utf-8")


async def test_find_glob_one_level(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _tree(workspace)
    assert await ft.find.ainvoke({"pattern": "*.md", "path": "notes"}) == "a.md"


async def test_find_glob_recursive(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _tree(workspace)
    assert await ft.find.ainvoke({"pattern": "**/*.md"}) == "notes/a.md\nnotes/sub/c.md"


async def test_find_substring_pattern(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _tree(workspace)
    assert await ft.find.ainvoke({"pattern": "*b*", "path": "notes"}) == "b.txt"


async def test_find_returns_files_only(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _tree(workspace)
    assert await ft.find.ainvoke({"pattern": "*", "path": "notes"}) == "a.md\nb.txt"


async def test_find_nothing(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _tree(workspace)
    assert await ft.find.ainvoke({"pattern": "*.pdf"}) == "(ничего не найдено)"


async def test_find_escaping_pattern_stays_in_zone(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _tree(workspace)
    (workspace.parent / "outside.md").write_text("o", encoding="utf-8")
    assert await ft.find.ainvoke({"pattern": "../*"}) == "(ничего не найдено)"
    with pytest.raises(FileAccessError):
        await ft.find.ainvoke({"pattern": "/etc/*"})


async def test_subagent_find_in_workspace_view(env, monkeypatch) -> None:
    workspace, checkpoints, subagent = env
    _patch(
        monkeypatch,
        workspace=workspace,
        checkpoints=checkpoints,
        agent_scope="subagent",
        subagent=subagent,
    )
    _tree(workspace)
    assert await ft.find.ainvoke({"pattern": "*.md", "path": "workspace/notes"}) == "a.md"


# --- grep ----------------------------------------------------------------------------


def _grep_tree(workspace: Path) -> None:
    (workspace / "notes" / "a.md").write_text("Alpha\nbeta\nalpha beta\n", encoding="utf-8")
    (workspace / "notes" / "b.txt").write_text("alpha\n", encoding="utf-8")
    (workspace / "notes" / "bin.dat").write_bytes(b"\xff\xfe\x00alpha")
    (workspace / ".runtime" / "x.md").write_text("alpha\n", encoding="utf-8")


async def test_grep_all_matches_in_file(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _grep_tree(workspace)
    out = await ft.grep.ainvoke({"pattern": "beta", "glob": "*.md"})
    assert out == "notes/a.md:2: beta\nnotes/a.md:3: alpha beta"


async def test_grep_ignore_case(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _grep_tree(workspace)
    out = await ft.grep.ainvoke({"pattern": "alpha", "glob": "*.md", "ignore_case": True})
    assert out == "notes/a.md:1: Alpha\nnotes/a.md:3: alpha beta"


async def test_grep_regex(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _grep_tree(workspace)
    assert await ft.grep.ainvoke({"pattern": "^beta$"}) == "notes/a.md:2: beta"


async def test_grep_glob_filter(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _grep_tree(workspace)
    assert await ft.grep.ainvoke({"pattern": "alpha", "glob": "*.txt"}) == "notes/b.txt:1: alpha"


async def test_grep_single_file_path(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _grep_tree(workspace)
    out = await ft.grep.ainvoke({"pattern": "beta", "path": "notes/a.md"})
    assert out == "a.md:2: beta\na.md:3: alpha beta"


async def test_grep_invalid_regex_raises(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _grep_tree(workspace)
    with pytest.raises(FileAccessError, match=r"missing \)"):
        await ft.grep.ainvoke({"pattern": "("})


async def test_grep_skips_binary_and_runtime(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _grep_tree(workspace)
    out = await ft.grep.ainvoke({"pattern": "alpha"})
    assert out == "notes/a.md:3: alpha beta\nnotes/b.txt:1: alpha"


async def test_grep_nothing(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _grep_tree(workspace)
    assert await ft.grep.ainvoke({"pattern": "gamma"}) == "(ничего не найдено)"


# --- edit ----------------------------------------------------------------------------


async def test_edit_single_replace(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    (workspace / "c.md").write_text("foo bar baz", encoding="utf-8")
    await ft.edit.ainvoke({"path": "c.md", "old_string": "bar", "new_string": "QUX"})
    assert (workspace / "c.md").read_text(encoding="utf-8") == "foo QUX baz"


async def test_edit_ambiguous_raises_and_no_write(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    (workspace / "d.md").write_text("x x x", encoding="utf-8")
    with pytest.raises(FileAccessError):
        await ft.edit.ainvoke({"path": "d.md", "old_string": "x", "new_string": "Y"})
    assert (workspace / "d.md").read_text(encoding="utf-8") == "x x x"  # файл не тронут
    assert not checkpoints.exists()  # ошибка до записи → снимка нет


async def test_edit_replace_all(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    (workspace / "e.md").write_text("x x x", encoding="utf-8")
    await ft.edit.ainvoke(
        {"path": "e.md", "old_string": "x", "new_string": "Y", "replace_all": True}
    )
    assert (workspace / "e.md").read_text(encoding="utf-8") == "Y Y Y"


async def test_edit_missing_file_raises(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(FileAccessError):
        await ft.edit.ainvoke({"path": "ghost.md", "old_string": "a", "new_string": "b"})


# --- rm ------------------------------------------------------------------------------


async def test_delete_file(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    (workspace / "g.md").write_text("g", encoding="utf-8")
    await ft.rm.ainvoke({"path": "g.md"})
    assert not (workspace / "g.md").exists()
    assert _checkpoints(workspace, checkpoints) == ["c000_rm"]


async def test_delete_missing_raises(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(FileAccessError):
        await ft.rm.ainvoke({"path": "ghost.md"})


# --- субагентский скоуп --------------------------------------------------------------


async def test_subagent_writes_to_own_zone(env, monkeypatch) -> None:
    workspace, checkpoints, subagent = env
    _patch(
        monkeypatch,
        workspace=workspace,
        checkpoints=checkpoints,
        agent_scope="subagent",
        subagent=subagent,
    )
    await ft.write.ainvoke({"path": "notes/working.md", "content": "sub"})
    assert (subagent / "notes" / "working.md").read_text(encoding="utf-8") == "sub"
    assert not (workspace / "notes" / "working.md").exists()  # в workspace не попало


async def test_subagent_cannot_write_workspace(env, monkeypatch) -> None:
    workspace, checkpoints, subagent = env
    _patch(
        monkeypatch,
        workspace=workspace,
        checkpoints=checkpoints,
        agent_scope="subagent",
        subagent=subagent,
    )
    with pytest.raises(FileAccessError):
        await ft.write.ainvoke({"path": "workspace/notes/x.md", "content": "x"})


async def test_subagent_reads_workspace_readonly(env, monkeypatch) -> None:
    workspace, checkpoints, subagent = env
    _patch(
        monkeypatch,
        workspace=workspace,
        checkpoints=checkpoints,
        agent_scope="subagent",
        subagent=subagent,
    )
    (workspace / "notes" / "decisions.md").write_text("RO", encoding="utf-8")
    out = await ft.read.ainvoke({"path": "workspace/notes/decisions.md"})
    assert out == "RO"


# --- truncation of large output ------------------------------------------------------


def _big_log(workspace: Path, lines: int = 3000) -> Path:
    log = workspace / "app.log"
    log.write_text("".join(f"{i} ERROR boom\n" for i in range(1, lines + 1)), encoding="utf-8")
    return log


async def test_grep_large_output_is_cut_and_spilled(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _big_log(workspace)

    out = await ft.grep.ainvoke({"pattern": "ERROR", "path": "app.log"})
    assert out.splitlines()[0] == "app.log:1: 1 ERROR boom"
    # grep lines are long enough that the 50 KB limit bites before the line limit.
    assert "из 3000 строк, 50 КБ из" in out.splitlines()[-1]
    spills = list((workspace / SPILL_DIR).iterdir())
    assert len(spills) == 1 and spills[0].name.startswith("grep-")


async def test_read_large_file_is_cut_without_spill(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _big_log(workspace)

    out = await ft.read.ainvoke({"path": "app.log"})
    assert "offset=2001" in out.splitlines()[-1]
    assert not (workspace / SPILL_DIR).exists()


async def test_read_window_cut_does_not_lose_a_line(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    _big_log(workspace)

    out = await ft.read.ainvoke({"path": "app.log", "offset": 1, "limit": 3000})
    lines = out.splitlines()
    assert lines[0] == "[строки 1–2000 из 3000]"
    assert lines[-2] == "2000 ERROR boom"
    assert lines[-1].endswith("Продолжай с offset=2001.]")


async def test_spill_is_readable_but_hidden_from_ls_find_grep(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    (workspace / SPILL_DIR).mkdir()
    (workspace / SPILL_DIR / "grep-1.txt").write_text("needle", encoding="utf-8")

    assert await ft.read.ainvoke({"path": f"{SPILL_DIR}/grep-1.txt"}) == "needle"
    assert SPILL_DIR not in await ft.ls.ainvoke({"path": "."})
    assert await ft.find.ainvoke({"pattern": "**/*.txt"}) == "(ничего не найдено)"
    assert await ft.grep.ainvoke({"pattern": "needle"}) == "(ничего не найдено)"
