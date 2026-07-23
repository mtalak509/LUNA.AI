"""Тесты раннера AgentSession: turn-task, очередь, resolve/cancel."""

from __future__ import annotations

import asyncio

import pytest

from core.agent.session import AgentSession
from core.agent.base import StreamEvent
from core.agent.tools import _hitl
from core.agent.tools._hitl import HitlError, HitlRegistry, set_hitl_registry


class FakeAgent:
    """Подмена BaseAgent: run_stream делегирует инжектированной async-gen фабрике."""

    def __init__(self, gen_factory):
        self._gen_factory = gen_factory

    def run_stream(self, text, *, permission_mode, decision_mode, session_id, pointer=None):
        return self._gen_factory(text)


def _session(gen_factory, session_id: str = "s1") -> AgentSession:
    return AgentSession(
        FakeAgent(gen_factory),
        session_id=session_id,
        profile="standalone",
        permission_mode="act",
        decision_mode="accept_all",
    )


async def _next(gen):
    """Следующее событие с таймаутом — чтобы упавший тест не висел вечно."""
    return await asyncio.wait_for(anext(gen), timeout=1.0)


@pytest.fixture
def registry():
    reg = HitlRegistry()
    set_hitl_registry(reg)
    yield reg
    _hitl._registry = None


# --- базовый поток -----------------------------------------------------------

async def test_events_flow_to_queue_then_turn_done():
    async def gen(text):
        yield StreamEvent("main", "messages", {"chunk": "hi"})

    session = _session(gen)
    turn_id = session.run_turn("hello")
    events = session.events()

    e1 = await _next(events)
    assert e1.mode == "messages"
    assert e1.data == {"chunk": "hi"}

    e2 = await _next(events)
    assert e2.mode == "control"
    assert e2.data == {"type": "turn_done", "turn_id": turn_id}


async def test_run_turn_returns_incrementing_ids():
    async def gen(text):
        yield StreamEvent("main", "messages", {"x": 1})

    session = _session(gen)
    id1 = session.run_turn("a")
    # дождаться завершения первого хода, чтобы второй не отбился гардом
    await asyncio.wait_for(session._turn_task, timeout=1.0)
    id2 = session.run_turn("b")
    assert (id1, id2) == ("turn-1", "turn-2")
    await asyncio.wait_for(session._turn_task, timeout=1.0)


async def test_concurrent_turn_rejected():
    async def gen(text):
        await asyncio.Event().wait()  # висит вечно
        yield StreamEvent("main", "messages", {})

    session = _session(gen)
    session.run_turn("a")
    with pytest.raises(RuntimeError):
        session.run_turn("b")
    session.cancel_turn()  # прибрать висящий Task


# --- HITL-парковка + внешний резолв ------------------------------------------

async def test_resolve_hitl_unblocks_parked_turn(registry):
    async def gen(text):
        hitl_id, fut = registry.register("s1")
        yield StreamEvent("main", "custom", {"type": "hitl_question", "hitl_id": hitl_id})
        value = await fut  # ход припаркован
        yield StreamEvent("main", "messages", {"answer": value})

    session = _session(gen)
    session.run_turn("go")
    events = session.events()

    q = await _next(events)
    assert q.data["type"] == "hitl_question"

    # пока ход припаркован — потребитель свободен резолвнуть извне
    session.resolve_hitl(q.data["hitl_id"], {"value": "42"})

    ans = await _next(events)
    assert ans.data == {"answer": {"value": "42"}}
    done = await _next(events)
    assert done.data["type"] == "turn_done"


def test_resolve_hitl_unknown_raises(registry):
    session = _session(None)
    with pytest.raises(HitlError):
        session.resolve_hitl("nope", "x")


async def test_resolve_hitl_foreign_session_raises(registry):
    """hitl-запись чужой сессии для resolve_hitl неотличима от неизвестного id."""
    hitl_id, _ = registry.register("other-session")
    session = _session(None)  # session_id="s1"
    with pytest.raises(HitlError):
        session.resolve_hitl(hitl_id, "x")
    assert hitl_id in registry.pending_ids("other-session")  # запись владельца цела


# --- отмена хода (аналог v1 api/chat/stop) ------------------------------------

async def test_cancel_turn_emits_cancelled_and_done():
    async def gen(text):
        yield StreamEvent("main", "messages", {"x": 1})
        await asyncio.Event().wait()  # парк навсегда
        yield StreamEvent("main", "messages", {"never": True})

    session = _session(gen)
    session.run_turn("go")
    events = session.events()

    first = await _next(events)
    assert first.data == {"x": 1}

    assert session.cancel_turn() is True

    e = await _next(events)
    assert e.data["type"] == "turn_cancelled"
    e = await _next(events)
    assert e.data["type"] == "turn_done"

    await asyncio.sleep(0)  # дать Task финализироваться
    assert session.cancel_turn() is False  # гасить больше нечего


async def test_cancel_turn_noop_when_idle():
    async def gen(text):
        yield StreamEvent("main", "messages", {})

    session = _session(gen)
    assert session.cancel_turn() is False
