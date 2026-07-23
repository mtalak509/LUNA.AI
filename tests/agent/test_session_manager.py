"""Тесты SessionManager: create/get/list/delete, delete при активном ходе."""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from core.agent.base import StreamEvent
from core.agent.session import SessionManager


class FakeAgent:
    """Подмена BaseAgent: run_stream делегирует инжектированной async-gen фабрике."""

    def __init__(self, gen_factory):
        self._gen_factory = gen_factory

    def run_stream(self, text, *, permission_mode, decision_mode, session_id, pointer=None):
        return self._gen_factory(text)


async def _one_event_gen(text):
    yield StreamEvent("main", "messages", {"chunk": "hi"})


async def _hang_forever_gen(text):
    await asyncio.Event().wait()  # висит вечно (до cancel)
    yield StreamEvent("main", "messages", {"never": True})


def _manager(tmp_path: Path, gen_factory=_one_event_gen) -> SessionManager:
    """Менеджер с фейковыми фабриками — сборка агента (bootstrap/конфиг) не нужна."""
    factory = lambda session_root: FakeAgent(gen_factory)  # noqa: E731
    return SessionManager(tmp_path, agent_factories={"standalone": factory, "custom": factory})


# --- create -------------------------------------------------------------------

def test_create_mints_id_and_dir_and_registers(tmp_path):
    manager = _manager(tmp_path)
    session_id, session = manager.create_session("standalone")

    assert (tmp_path / session_id).is_dir()
    assert manager.get_session(session_id) is session
    assert session.session_id == session_id
    # дефолтные режимы новой сессии
    assert (session.permission_mode, session.decision_mode) == ("act", "confirm")


def test_create_passes_session_root_to_factory(tmp_path):
    seen: list[Path] = []

    def factory(session_root: Path) -> FakeAgent:
        seen.append(session_root)
        return FakeAgent(_one_event_gen)

    manager = SessionManager(tmp_path, agent_factories={"standalone": factory})
    session_id, _ = manager.create_session("standalone")
    assert seen == [tmp_path / session_id]


def test_create_ids_unique(tmp_path):
    manager = _manager(tmp_path)
    ids = {manager.create_session("standalone")[0] for _ in range(5)}
    assert len(ids) == 5


def test_create_stores_profile_per_session(tmp_path):
    """Профиль сессии = аргумент create, не глобальный дефолт (регрессия AR-2)."""
    manager = _manager(tmp_path)
    _, custom = manager.create_session("custom")
    _, standalone = manager.create_session("standalone")
    assert custom.profile == "custom"
    assert standalone.profile == "standalone"
    assert {e["profile"] for e in manager.list_sessions()} == {"custom", "standalone"}


def test_create_unknown_profile_raises_before_disk(tmp_path):
    """Неизвестный профиль → ValueError ДО mkdir: на диске не остаётся сироты."""
    manager = _manager(tmp_path)
    with pytest.raises(ValueError):
        manager.create_session("wtf")
    assert list(tmp_path.iterdir()) == []


def test_create_refuses_to_reuse_existing_dir(tmp_path, monkeypatch):
    """Paranoia-check: коллизия id с живой директорией → ошибка, а не молчаливое затирание."""
    manager = _manager(tmp_path)
    fixed = uuid.uuid4()
    monkeypatch.setattr("core.agent.session.uuid.uuid4", lambda: fixed)
    (tmp_path / fixed.hex).mkdir()
    with pytest.raises(FileExistsError):
        manager.create_session("standalone")


# --- get / list ---------------------------------------------------------------

def test_get_unknown_returns_none(tmp_path):
    assert _manager(tmp_path).get_session("nope") is None


def test_list_sessions_fields(tmp_path):
    manager = _manager(tmp_path)
    session_id, session = manager.create_session("standalone")

    (listing,) = manager.list_sessions()
    assert listing["session_id"] == session_id
    assert listing["is_busy"] is False
    assert listing["permission_mode"] == "act"
    assert listing["decision_mode"] == "confirm"
    # обе метки времени — ISO-строки (голый datetime не пережил бы json.dumps)
    assert listing["created_at"] == str(listing["created_at"])
    assert listing["last_activity"] == str(listing["last_activity"])


def test_list_sessions_empty(tmp_path):
    assert _manager(tmp_path).list_sessions() == []


# --- delete -------------------------------------------------------------------

async def test_delete_removes_registry_and_disk(tmp_path):
    manager = _manager(tmp_path)
    session_id, _ = manager.create_session("standalone")

    await manager.delete_session(session_id)

    assert manager.get_session(session_id) is None
    assert not (tmp_path / session_id).exists()
    assert manager.list_sessions() == []


async def test_delete_unknown_raises_keyerror(tmp_path):
    """Неизвестный id → KeyError (server.py мапит в 404)."""
    with pytest.raises(KeyError):
        await _manager(tmp_path).delete_session("nope")


async def test_delete_cancels_active_turn(tmp_path):
    """delete при активном ходе: ход гасится, Task дожидается, диск чистится."""
    manager = _manager(tmp_path, gen_factory=_hang_forever_gen)
    session_id, session = manager.create_session("standalone")
    session.run_turn("go")
    assert session.is_busy

    await asyncio.wait_for(manager.delete_session(session_id), timeout=1.0)

    assert not session.is_busy  # Task хода завершён, не осиротел
    assert manager.get_session(session_id) is None
    assert not (tmp_path / session_id).exists()
    # finally-цепочка _pump отработала: события отмены дошли до очереди
    events = session.events()
    e1 = await asyncio.wait_for(anext(events), timeout=1.0)
    assert e1.data["type"] == "turn_cancelled"
    e2 = await asyncio.wait_for(anext(events), timeout=1.0)
    assert e2.data["type"] == "turn_done"


async def test_delete_idle_session_is_clean(tmp_path):
    """delete без активного хода — cancel_turn() no-op, wait_turn() не виснет."""
    manager = _manager(tmp_path)
    session_id, _ = manager.create_session("standalone")
    await asyncio.wait_for(manager.delete_session(session_id), timeout=1.0)
    assert manager.list_sessions() == []
