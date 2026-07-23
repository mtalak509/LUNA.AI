"""
Unit-тесты path-based смарт-тулов JSON (json_inspect / json_search / json_patch).

Тулы гоняются через `.ainvoke({...})` — реальный путь вызова. Рантайм-контекст (get_runtime)
подменяется monkeypatch'ем в ДВУХ модулях: json_tools (PathScope) и checkpoint (снимок).
get_stream_writer тоже подменяется (вне графа он кинул бы RuntimeError) — на коллектор событий.
Граф create_agent не поднимаем.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent import checkpoint as cp
from core.agent.checkpoint import CheckpointManager
from core.agent.tools import json_tools as jt
from core.agent.tools.json_doc import JsonError

_DOC = "doc.json"


def _sample() -> dict:
    return {
        "part": {"id": "ABC-123", "material": "Сталь 45"},
        "route": [
            {"op": "Токарная", "machine": "16К20"},
            {"op": "Фрезерная", "machine": "6Р13"},
        ],
        "meta": {"version": 2, "approved": True, "note": None},
    }


@pytest.fixture()
def env(tmp_path: Path) -> tuple[Path, Path, Path]:
    """(workspace, checkpoints, subagent_zone) с записанным workspace/doc.json."""
    workspace = tmp_path / "session" / "workspace"
    checkpoints = tmp_path / "checkpoints"
    subagent = tmp_path / "session" / "subagents" / "uid"
    (workspace / ".runtime").mkdir(parents=True)
    (subagent / ".runtime").mkdir(parents=True)
    (workspace / _DOC).write_text(
        json.dumps(_sample(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return workspace, checkpoints, subagent


def _patch(
    monkeypatch: pytest.MonkeyPatch,
    *,
    workspace: Path,
    checkpoints: Path,
    agent_scope: str = "main",
    subagent: Path | None = None,
) -> list[dict]:
    """Подменяет рантайм-контекст + stream-writer. Возвращает список перехваченных событий."""
    events: list[dict] = []
    ctx = SimpleNamespace(
        agent_scope=agent_scope,
        workspace_path=workspace,
        checkpoints_root=checkpoints,
        subagent_path=subagent,
    )
    rt = SimpleNamespace(context=ctx)
    monkeypatch.setattr(jt, "get_runtime", lambda *a, **k: rt)
    monkeypatch.setattr(cp, "get_runtime", lambda *a, **k: rt)
    monkeypatch.setattr(jt, "get_stream_writer", lambda *a, **k: events.append)
    return events


def _checkpoints(workspace: Path, checkpoints: Path) -> list[str]:
    return [c.id for c in CheckpointManager(
        workspace_path=workspace, checkpoints_root=checkpoints
    ).list()]


def _doc(workspace: Path) -> dict:
    return json.loads((workspace / _DOC).read_text(encoding="utf-8"))


# --- json_inspect -------------------------------------------------------------------


async def test_inspect_root_map(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    out = await jt.json_inspect.ainvoke({"file": _DOC, "pointer": ""})
    assert '"part": object: 2 ключей' in out
    assert '"route": array: 2 элементов' in out
    assert '"meta": object: 3 ключей' in out


async def test_inspect_nested_shows_scalars(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    out = await jt.json_inspect.ainvoke({"file": _DOC, "pointer": "/part"})
    assert out == '"id": "ABC-123"\n"material": "Сталь 45"'


async def test_inspect_scalar_pointer(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    assert await jt.json_inspect.ainvoke({"file": _DOC, "pointer": "/part/material"}) == '"Сталь 45"'


async def test_inspect_default_pointer_is_root(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    out = await jt.json_inspect.ainvoke({"file": _DOC})  # pointer по умолчанию = ""
    assert '"part": object: 2 ключей' in out


async def test_inspect_miss_raises(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(JsonError):
        await jt.json_inspect.ainvoke({"file": _DOC, "pointer": "/route/99"})


async def test_inspect_missing_file_raises(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(JsonError):
        await jt.json_inspect.ainvoke({"file": "nope.json"})


async def test_inspect_max_children_caps(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    out = await jt.json_inspect.ainvoke({"file": _DOC, "pointer": "/meta", "max_children": 1})
    assert out.splitlines()[-1] == "… ещё 2"


# --- json_search --------------------------------------------------------------------


async def test_search_value_match(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    out = await jt.json_search.ainvoke({"file": _DOC, "query": "сталь"})
    assert out == '/part/material: "Сталь 45"'


async def test_search_path_hint_scopes(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    out = await jt.json_search.ainvoke({"file": _DOC, "query": "version", "path_hint": "/meta"})
    assert out == "/meta/version: 2"


async def test_search_no_match(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    assert await jt.json_search.ainvoke({"file": _DOC, "query": "zzz"}) == "(ничего не найдено)"


async def test_search_empty_query_raises(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(JsonError):
        await jt.json_search.ainvoke({"file": _DOC, "query": "   "})


# --- субагент: RO-чтение JSON из workspace главного агента через workspace/ ----------


async def test_subagent_reads_workspace_doc(env, monkeypatch) -> None:
    workspace, checkpoints, subagent = env
    _patch(
        monkeypatch,
        workspace=workspace,
        checkpoints=checkpoints,
        agent_scope="subagent",
        subagent=subagent,
    )
    # документа в зоне субагента нет — читается из workspace главного агента через RO-вид
    out = await jt.json_inspect.ainvoke({"file": f"workspace/{_DOC}", "pointer": "/part/material"})
    assert out == '"Сталь 45"'


# --- json_patch ---------------------------------------------------------------------


async def test_patch_applies_and_summarizes(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    res = await jt.json_patch.ainvoke(
        {"file": _DOC, "operations": [{"op": "replace", "path": "/part/material", "value": "Латунь"}]}
    )
    assert res == "Применено операций: 1"
    assert _doc(workspace)["part"]["material"] == "Латунь"


async def test_patch_triggers_checkpoint(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    await jt.json_patch.ainvoke(
        {"file": _DOC, "operations": [{"op": "replace", "path": "/meta/version", "value": 3}]}
    )
    assert _checkpoints(workspace, checkpoints) == ["c000_json_patch"]


async def test_patch_emits_document_patch(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    events = _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    ops = [{"op": "replace", "path": "/meta/version", "value": 3}]
    await jt.json_patch.ainvoke({"file": _DOC, "operations": ops})
    assert events == [{"type": "document_patch", "file": _DOC, "operations": ops}]


async def test_patch_copy_op(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    await jt.json_patch.ainvoke(
        {"file": _DOC, "operations": [{"op": "copy", "from": "/route/0", "path": "/route/-"}]}
    )
    route = _doc(workspace)["route"]
    assert len(route) == 3 and route[2] == route[0]


async def test_patch_bad_op_no_write_no_checkpoint(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    events = _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(JsonError):
        await jt.json_patch.ainvoke(
            {"file": _DOC, "operations": [{"op": "replace", "path": "/missing/deep", "value": 1}]}
        )
    assert _doc(workspace) == _sample()        # документ цел
    assert _checkpoints(workspace, checkpoints) == []  # снимка нет
    assert events == []                         # document_patch не эмитился


async def test_patch_empty_ops_raises(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)
    with pytest.raises(JsonError):
        await jt.json_patch.ainvoke({"file": _DOC, "operations": []})
    assert _checkpoints(workspace, checkpoints) == []


async def test_patch_emit_failure_does_not_break_tool(env, monkeypatch) -> None:
    workspace, checkpoints, _ = env
    _patch(monkeypatch, workspace=workspace, checkpoints=checkpoints)

    def _boom(*a, **k):
        raise RuntimeError("stream down")

    monkeypatch.setattr(jt, "get_stream_writer", _boom)
    # запись уже состоялась → сбой стрима не должен ронять тул
    res = await jt.json_patch.ainvoke(
        {"file": _DOC, "operations": [{"op": "replace", "path": "/meta/version", "value": 9}]}
    )
    assert res == "Применено операций: 1"
    assert _doc(workspace)["meta"]["version"] == 9
