"""Тесты HITL-канала: реестр futures + request_human."""

from __future__ import annotations

import asyncio
import json

import pytest
from langchain_core.tools import ToolException

from core.agent.tools import _hitl
from core.agent.tools._hitl import (
    HitlError,
    HitlRegistry,
    get_hitl_registry,
    request_human,
    set_hitl_registry,
)


@pytest.fixture
def registry():
    """Свежий реестр в холдер на каждый тест; сброс после (процессный синглтон)."""
    reg = HitlRegistry()
    set_hitl_registry(reg)
    yield reg
    _hitl._registry = None


@pytest.fixture
def captured_events(monkeypatch):
    """Перехват best-effort эмита (вне графа get_stream_writer недоступен)."""
    events: list[dict] = []
    monkeypatch.setattr(_hitl, "_emit_hitl_event", events.append)
    return events


# --- реестр ------------------------------------------------------------------

def test_get_registry_uninitialized_raises():
    _hitl._registry = None
    with pytest.raises(ToolException):
        get_hitl_registry()


@pytest.mark.asyncio
async def test_register_returns_unique_ids(registry):
    id1, f1 = registry.register("s1")
    id2, f2 = registry.register("s1")
    assert id1 != id2
    assert f1 is not f2
    assert set(registry.pending_ids()) == {id1, id2}


@pytest.mark.asyncio
async def test_resolve_sets_result_and_pops(registry):
    hitl_id, fut = registry.register("s1")
    registry.resolve(hitl_id, {"value": 42}, "s1")
    assert await fut == {"value": 42}
    assert hitl_id not in registry.pending_ids()


def test_resolve_unknown_id_raises(registry):
    with pytest.raises(HitlError):
        registry.resolve("nope", "x", "s1")


@pytest.mark.asyncio
async def test_resolve_twice_is_safe(registry):
    hitl_id, fut = registry.register("s1")
    registry.resolve(hitl_id, "a", "s1")
    # второй /hitl/respond по тому же id — уже popнут → HitlError, future не трогается
    with pytest.raises(HitlError):
        registry.resolve(hitl_id, "b", "s1")
    assert await fut == "a"


# --- session-скоуп ------------------------------------------------------

@pytest.mark.asyncio
async def test_resolve_foreign_session_raises_and_keeps_entry(registry):
    """Чужая сессия == неизвестный id; запись НЕ снимается — владелец резолвит позже."""
    hitl_id, fut = registry.register("s1")
    with pytest.raises(HitlError):
        registry.resolve(hitl_id, "hijack", "s2")
    # запись цела, законный владелец резолвит штатно
    assert hitl_id in registry.pending_ids("s1")
    registry.resolve(hitl_id, "ok", "s1")
    assert await fut == "ok"


@pytest.mark.asyncio
async def test_pending_ids_filters_by_session(registry):
    id1, _ = registry.register("s1")
    id2, _ = registry.register("s2")
    assert registry.pending_ids("s1") == [id1]
    assert registry.pending_ids("s2") == [id2]
    assert set(registry.pending_ids()) == {id1, id2}  # None → весь процесс (/health)


# --- request_human -----------------------------------------------------------

@pytest.mark.asyncio
async def test_request_human_registers_emits_and_awaits(registry, captured_events, tmp_path):
    task = asyncio.create_task(
        request_human(
            "ask",
            {"question": "Сколько?", "context_for_main": "ctx", "allow_main_dismissal": True},
            namespace="main",
            session_id="s1",
            workspace_path=tmp_path,
            write_marker=False,
        )
    )
    # даём корутине дойти до await fut
    await asyncio.sleep(0)

    assert len(captured_events) == 1
    ev = captured_events[0]
    assert ev["type"] == "hitl_question"
    assert ev["kind"] == "ask"
    assert ev["namespace"] == "main"
    assert ev["question"] == "Сколько?"
    hitl_id = ev["hitl_id"]
    assert hitl_id in registry.pending_ids()

    registry.resolve(hitl_id, {"value": "5", "source": "user"}, "s1")
    assert await task == {"value": "5", "source": "user"}
    assert hitl_id not in registry.pending_ids()


@pytest.mark.asyncio
async def test_request_human_confirm_emits_confirm_type(registry, captured_events, tmp_path):
    task = asyncio.create_task(
        request_human(
            "confirm",
            {"action_description": "Применить патч?"},
            namespace="main",
            session_id="s1",
            workspace_path=tmp_path,
            write_marker=False,
        )
    )
    await asyncio.sleep(0)
    assert captured_events[0]["type"] == "hitl_confirm"
    registry.resolve(captured_events[0]["hitl_id"], True, "s1")
    assert await task is True


@pytest.mark.asyncio
async def test_marker_written_for_main_and_cleared(registry, captured_events, tmp_path):
    marker = tmp_path / ".runtime" / "hitl_pending.json"
    task = asyncio.create_task(
        request_human(
            "ask", {"question": "q"},
            namespace="main", session_id="s1", workspace_path=tmp_path, write_marker=True,
        )
    )
    await asyncio.sleep(0)
    # висит вопрос → маркер на диске с корректным содержимым
    assert marker.exists()
    saved = json.loads(marker.read_text(encoding="utf-8"))
    assert saved["hitl_id"] == captured_events[0]["hitl_id"]
    assert saved["kind"] == "ask"

    registry.resolve(captured_events[0]["hitl_id"], "ok", "s1")
    await task
    # ответ пришёл → маркер снят
    assert not marker.exists()


@pytest.mark.asyncio
async def test_marker_not_written_for_subagent(registry, captured_events, tmp_path):
    task = asyncio.create_task(
        request_human(
            "ask", {"question": "q"},
            namespace="subagent.default.abc123", session_id="s1",
            workspace_path=tmp_path, write_marker=False,
        )
    )
    await asyncio.sleep(0)
    assert not (tmp_path / ".runtime" / "hitl_pending.json").exists()
    registry.resolve(captured_events[0]["hitl_id"], "ok", "s1")
    await task


@pytest.mark.asyncio
async def test_request_human_cleans_up_on_cancel(registry, captured_events, tmp_path):
    task = asyncio.create_task(
        request_human(
            "ask", {"question": "q"},
            namespace="main", session_id="s1", workspace_path=tmp_path, write_marker=True,
        )
    )
    await asyncio.sleep(0)
    hitl_id = captured_events[0]["hitl_id"]
    assert hitl_id in registry.pending_ids()

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # finally отработал: реестр чист, маркер снят
    assert hitl_id not in registry.pending_ids()
    assert not (tmp_path / ".runtime" / "hitl_pending.json").exists()
