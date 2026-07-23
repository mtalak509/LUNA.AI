"""
Тесты FastAPI-сервера MainAgent — мультисессия.

lifespan (init_process → GPUStack/сеть) в тестах НЕ поднимаем: httpx ASGITransport не гоняет
lifespan-протокол, поэтому app.state.sessions подставляем вручную. Маршруты юнит-тестируем
против FakeSessionManager/FakeSession (сервер — тонкий маппер); проводку create→turn→delete
через реальный SessionManager с фейковой фабрикой агента — отдельным интеграционным блоком.
SSE-хелперы тестируем как юниты.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage

from core.agent.base import StreamEvent
from core.agent.session import SessionManager
from core.app import server, sse
from core.app.routes import dev as dev_routes
from core.app.server import create_app
from core.app.sse import _sse_frame, _sse_stream
from core.agent.tools._hitl import HitlError


class FakeReloadAgent:
    """Подмена BaseAgent: считает вызовы reload_history (ре-гидратация после restore)."""

    def __init__(self) -> None:
        self.reload_calls = 0

    def reload_history(self) -> None:
        self.reload_calls += 1


class FakeSession:
    """Подмена AgentSession: фиксирует вызовы, управляемо кидает ошибки, отдаёт события."""

    def __init__(self, profile: str = "standalone") -> None:
        self.runs: list[str] = []
        self.pointers: list[str | None] = []
        self.resolved: list[tuple] = []
        self.cancel_result = True
        self.raise_on_run: Exception | None = None
        self.raise_on_resolve: Exception | None = None
        self._events: asyncio.Queue = asyncio.Queue()
        self.is_busy = False
        self.profile = profile
        self.permission_mode = "act"
        self.decision_mode = "confirm"
        self._agent = FakeReloadAgent()

    def run_turn(self, text: str, pointer: str | None = None) -> str:
        if self.raise_on_run:
            raise self.raise_on_run
        self.runs.append(text)
        self.pointers.append(pointer)
        return "turn-1"

    def resolve_hitl(self, hitl_id, value) -> None:
        if self.raise_on_resolve:
            raise self.raise_on_resolve
        self.resolved.append((hitl_id, value))

    def cancel_turn(self) -> bool:
        return self.cancel_result

    async def events(self):
        while True:
            yield await self._events.get()


class FakeSessionManager:
    """Подмена SessionManager: реестр в dict, диск не трогает (кроме листинга-заглушки)."""

    def __init__(self) -> None:
        self.sessions: dict[str, FakeSession] = {}
        self.deleted: list[str] = []
        self._counter = 0

    def create_session(self, profile: str):
        self._counter += 1
        session_id = f"fake-{self._counter}"
        session = FakeSession(profile=profile)
        self.sessions[session_id] = session
        return session_id, session

    def get_session(self, session_id: str):
        return self.sessions.get(session_id)

    def list_sessions(self) -> list[dict]:
        return [
            {"session_id": sid, "profile": s.profile, "is_busy": s.is_busy, "created_at": "t0"}
            for sid, s in self.sessions.items()
        ]

    async def delete_session(self, session_id: str) -> None:
        if session_id not in self.sessions:
            raise KeyError(session_id)
        del self.sessions[session_id]
        self.deleted.append(session_id)


@pytest.fixture
def client_and_session(tmp_path):
    """Клиент + одна предсозданная фейковая сессия 's1' (workspace на диске под upload)."""
    app = create_app()
    manager = FakeSessionManager()
    session = FakeSession()
    manager.sessions["s1"] = session
    # Подменяем всё, что в проде поставил бы lifespan (ASGITransport lifespan не гоняет).
    app.state.sessions = manager
    app.state.session_root = tmp_path
    (tmp_path / "s1" / "workspace").mkdir(parents=True)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), session


# --- процесс -------------------------------------------------------------------

async def test_health(client_and_session):
    client, _ = client_and_session
    async with client:
        r = await client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert isinstance(body["pending_hitl"], int)
    assert body["sessions_count"] == 1


# --- жизненный цикл сессий -------------------------------------------------------

async def test_create_session_201(client_and_session):
    client, _ = client_and_session
    async with client:
        r = await client.post("/sessions", json={"profile": "standalone"})
    assert r.status_code == 201
    assert r.json() == {"session_id": "fake-1", "profile": "standalone"}


@pytest.mark.parametrize("body", [None, {}, {"profile": "wtf"}, {"profile": ""}])
async def test_create_session_invalid_profile_422(client_and_session, body):
    """профиль обязателен и без дефолта — контрактная ошибка видна сразу как 422."""
    client, _ = client_and_session
    async with client:
        kwargs = {"json": body} if body is not None else {}
        r = await client.post("/sessions", **kwargs)
    assert r.status_code == 422


def test_factories_registry_covers_all_profiles(tmp_path):
    """Синхронизатор: ключи реестра фабрик == значения SessionProfile (литералы
    продублированы сознательно — bootstrap не должен зависеть от HTTP-контракта)."""
    from typing import get_args

    from core.agent.bootstrap import build_agent_factory
    from core.models import SessionProfile

    assert set(build_agent_factory(tmp_path).keys()) == set(get_args(SessionProfile))


async def test_list_sessions(client_and_session):
    client, _ = client_and_session
    async with client:
        r = await client.get("/sessions")
    assert r.status_code == 200
    (listing,) = r.json()
    assert listing["session_id"] == "s1"
    assert listing["profile"] == "standalone"
    assert listing["is_busy"] is False


async def test_delete_session_204(client_and_session):
    client, _ = client_and_session
    async with client:
        r = await client.delete("/sessions/s1")
        assert r.status_code == 204
        # сессия исчезла из реестра — маршруты отвечают 404
        r2 = await client.get("/sessions/s1/mode")
    assert r2.status_code == 404


async def test_delete_unknown_session_404(client_and_session):
    client, _ = client_and_session
    async with client:
        r = await client.delete("/sessions/nope")
    assert r.status_code == 404


@pytest.mark.parametrize(
    "method, path, body",
    [
        ("post", "/sessions/nope/turn", {"text": "x"}),
        ("post", "/sessions/nope/hitl/respond", {"hitl_id": "h", "value": 1}),
        ("post", "/sessions/nope/stop", None),
        ("get", "/sessions/nope/mode", None),
        ("post", "/sessions/nope/mode", {"permission_mode": "plan"}),
        ("get", "/sessions/nope/events", None),
        ("get", "/sessions/nope/history", None),
    ],
)
async def test_unknown_session_id_404(client_and_session, method, path, body):
    """Все session-scoped маршруты: неизвестный id → 404."""
    client, _ = client_and_session
    async with client:
        kwargs = {"json": body} if body is not None else {}
        r = await getattr(client, method)(path, **kwargs)
    assert r.status_code == 404


# --- session-scoped маршруты хода --------------------------------------------------

async def test_turn_accepts_and_starts(client_and_session):
    client, session = client_and_session
    async with client:
        r = await client.post("/sessions/s1/turn", json={"text": "привет"})
    assert r.status_code == 202
    assert r.json() == {"turn_id": "turn-1"}
    assert session.runs == ["привет"]


async def test_turn_conflict_when_busy(client_and_session):
    client, session = client_and_session
    session.raise_on_run = RuntimeError("Another turn is already running")
    async with client:
        r = await client.post("/sessions/s1/turn", json={"text": "x"})
    assert r.status_code == 409


async def test_turn_passes_pointer(client_and_session):
    """pointer из тела доезжает до run_turn; без него — None (standalone)."""
    client, session = client_and_session
    async with client:
        r1 = await client.post(
            "/sessions/s1/turn", json={"text": "что в разделе?", "pointer": "/children/2"}
        )
        r2 = await client.post("/sessions/s1/turn", json={"text": "привет"})
    assert r1.status_code == 202 and r2.status_code == 202
    assert session.pointers == ["/children/2", None]


async def test_turn_attachments_typed(client_and_session):
    """Контракт-задел: валидные attachments принимаются, мусорные → 422."""
    client, session = client_and_session
    async with client:
        ok = await client.post(
            "/sessions/s1/turn",
            json={
                "text": "смотри сюда",
                "attachments": [{"type": "node_ref", "path": "/children/0", "label": "025"}],
            },
        )
        bad = await client.post(
            "/sessions/s1/turn",
            json={"text": "x", "attachments": [{"nonsense": True}]},  # нет type/path
        )
    assert ok.status_code == 202
    assert bad.status_code == 422
    assert session.runs == ["смотри сюда"]  # мусорный ход до run_turn не дошёл


async def test_hitl_respond_resolves(client_and_session):
    client, session = client_and_session
    async with client:
        r = await client.post(
            "/sessions/s1/hitl/respond",
            json={"hitl_id": "abc", "value": {"type": "selected", "id": "opt1"}},
        )
    assert r.status_code == 200
    assert r.json() == {"resolved": "abc"}
    assert session.resolved == [("abc", {"type": "selected", "id": "opt1"})]


async def test_hitl_respond_unknown_id_404(client_and_session):
    client, session = client_and_session
    session.raise_on_resolve = HitlError("неизвестный hitl_id")
    async with client:
        r = await client.post(
            "/sessions/s1/hitl/respond", json={"hitl_id": "nope", "value": "x"}
        )
    assert r.status_code == 404


async def test_stop_returns_cancelled_flag(client_and_session):
    client, session = client_and_session
    session.cancel_result = True
    async with client:
        r = await client.post("/sessions/s1/stop")
    assert r.status_code == 200
    assert r.json() == {"cancelled": True}


async def test_stop_nothing_to_cancel(client_and_session):
    client, session = client_and_session
    session.cancel_result = False
    async with client:
        r = await client.post("/sessions/s1/stop")
    assert r.json() == {"cancelled": False}


# --- /sessions/{id}/history ----------------------------------------------------

async def test_history_empty_session(client_and_session):
    """Свежая сессия без журнала → пустой вид, не 404/500."""
    client, _ = client_and_session
    async with client:
        r = await client.get("/sessions/s1/history")
    assert r.status_code == 200
    assert r.json() == {"summary": None, "messages": []}


async def test_history_returns_canonical_shape(client_and_session, tmp_path):
    """История — форма `{type, data}` (= updates-кадры SSE): human/ai/tool,
    tool_calls — структуры, не repr-строки."""
    from core.agent.messages import MessageStore

    client, _ = client_and_session
    store = MessageStore(tmp_path / "s1" / "workspace")
    store.append([
        HumanMessage("сделай патч"),
        AIMessage(
            content="",
            tool_calls=[{"name": "json_patch", "args": {"operations": []}, "id": "c1",
                         "type": "tool_call"}],
        ),
        ToolMessage(content="ok", tool_call_id="c1", name="json_patch", status="success"),
        AIMessage("готово"),
    ])

    async with client:
        r = await client.get("/sessions/s1/history")
    assert r.status_code == 200
    body = r.json()
    assert body["summary"] is None
    assert [m["type"] for m in body["messages"]] == ["human", "ai", "tool", "ai"]
    (tc,) = body["messages"][1]["data"]["tool_calls"]
    assert tc["name"] == "json_patch"
    assert tc["args"] == {"operations": []}
    assert body["messages"][2]["data"]["tool_call_id"] == "c1"
    assert body["messages"][3]["data"]["content"] == "готово"


async def test_history_with_compaction_summary(client_and_session, tmp_path):
    from core.agent.messages import MessageStore

    client, _ = client_and_session
    store = MessageStore(tmp_path / "s1" / "workspace")
    store.append([HumanMessage(f"m{i}") for i in range(3)])
    store.append_summary(HumanMessage("[Сводка предыдущего диалога]\n..."), head=2)

    async with client:
        r = await client.get("/sessions/s1/history")
    body = r.json()
    assert body["summary"]["data"]["content"].startswith("[Сводка")
    assert [m["data"]["content"] for m in body["messages"]] == ["m2"]


async def test_history_readable_while_busy(client_and_session, tmp_path):
    """Журнал append-only — история читается и при активном ходе (снапшот)."""
    from core.agent.messages import MessageStore

    client, session = client_and_session
    MessageStore(tmp_path / "s1" / "workspace").append([HumanMessage("в полёте")])
    session.is_busy = True

    async with client:
        r = await client.get("/sessions/s1/history")
    assert r.status_code == 200
    assert r.json()["messages"][0]["data"]["content"] == "в полёте"


# --- /sessions/{id}/mode -------------------------------------------------------------

async def test_get_mode_returns_current(client_and_session):
    client, session = client_and_session
    session.permission_mode = "plan"
    async with client:
        r = await client.get("/sessions/s1/mode")
    assert r.status_code == 200
    assert r.json() == {"permission_mode": "plan", "decision_mode": "confirm"}


async def test_set_mode_partial_update(client_and_session):
    client, session = client_and_session
    async with client:
        r = await client.post("/sessions/s1/mode", json={"decision_mode": "accept_all"})
    assert r.status_code == 200
    assert r.json() == {"permission_mode": "act", "decision_mode": "accept_all"}
    assert session.permission_mode == "act"  # не тронут
    assert session.decision_mode == "accept_all"


async def test_set_mode_both(client_and_session):
    client, session = client_and_session
    async with client:
        r = await client.post(
            "/sessions/s1/mode",
            json={"permission_mode": "plan", "decision_mode": "accept_all"},
        )
    assert r.json() == {"permission_mode": "plan", "decision_mode": "accept_all"}


async def test_set_mode_invalid_value_422(client_and_session):
    client, session = client_and_session
    async with client:
        r = await client.post("/sessions/s1/mode", json={"permission_mode": "yolo"})
    assert r.status_code == 422
    assert session.permission_mode == "act"  # ничего не изменилось


# --- root_path -----------------------------------------------------------------------

async def test_root_path_from_config(monkeypatch):
    """create_app пробрасывает server.root_path в FastAPI (жизнь под ingress-префиксом)."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        server, "get_config",
        lambda: SimpleNamespace(
            server=SimpleNamespace(root_path="/agent", allowed_origins=["*"])
        ),
    )
    app = create_app()
    assert app.root_path == "/agent"


# --- зачистка stale-сессий на старте -----------------------------------------------------

def test_wipe_stale_sessions_removes_dirs_keeps_files(tmp_path):
    """Директории прошлого процесса (сессии, procedures) сносятся, файлы в корне — нет."""
    (tmp_path / "deadbeef01" / "workspace").mkdir(parents=True)
    (tmp_path / "procedures").mkdir()
    stray = tmp_path / "stray.log"
    stray.write_text("x", encoding="utf-8")

    server._wipe_stale_sessions(tmp_path)

    assert not (tmp_path / "deadbeef01").exists()
    assert not (tmp_path / "procedures").exists()
    assert stray.exists()


def test_wipe_stale_sessions_missing_root_is_noop(tmp_path):
    server._wipe_stale_sessions(tmp_path / "nope")  # не существует → тихий no-op


async def test_lifespan_wipes_stale_sessions_before_init(tmp_path, monkeypatch):
    """Регрессия (devfront): после рестарта на диске оставались сессии-«сироты»,
    невидимые для GET /sessions. lifespan обязан зачистить диск ДО init_process
    (тот кладёт procedures/ в тот же корень)."""
    from types import SimpleNamespace

    stale = tmp_path / "deadbeef01"
    (stale / "workspace").mkdir(parents=True)

    monkeypatch.setattr(
        server, "get_config",
        lambda: SimpleNamespace(
            server=SimpleNamespace(
                session_root=str(tmp_path), root_path="", allowed_origins=["*"]
            )
        ),
    )

    def fake_init_process(root):
        # На момент init_process мусор уже зачищен — порядок вызовов верный.
        assert not stale.exists()
        procedures_root = root / "procedures"
        procedures_root.mkdir()
        return procedures_root

    monkeypatch.setattr(server, "init_process", fake_init_process)

    app = create_app()
    async with server.lifespan(app):
        assert not stale.exists()
        assert isinstance(app.state.sessions, SessionManager)


# --- проводка через реальный SessionManager ---------------------------------------------

class FakeAgent:
    """Подмена BaseAgent для реального SessionManager: ход висит до cancel."""

    def __init__(self, gen_factory):
        self._gen_factory = gen_factory

    def run_stream(self, text, *, permission_mode, decision_mode, session_id, pointer=None):
        return self._gen_factory(text)


async def _hang_forever_gen(text):
    await asyncio.Event().wait()
    yield StreamEvent("main", "messages", {"never": True})


@pytest.fixture
def client_real_manager(tmp_path):
    """Клиент поверх реального SessionManager (фейковая только сборка агента)."""
    app = create_app()
    app.state.session_root = tmp_path

    def factory(session_root):
        # как build_session_agent: workspace обязан существовать до первого хода
        (session_root / "workspace").mkdir(parents=True)
        return FakeAgent(_hang_forever_gen)

    app.state.sessions = SessionManager(
        tmp_path, agent_factories={"standalone": factory}
    )
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def test_two_sessions_lifecycle(client_real_manager, tmp_path):
    """Интеграция маршрутов с реальным реестром: create ×2 → turn в одной → delete busy."""
    client = client_real_manager
    async with client:
        s1 = (await client.post("/sessions", json={"profile": "standalone"})).json()["session_id"]
        s2 = await client.post("/sessions", json={"profile": "standalone"})
        assert s2.status_code == 201
        s2 = s2.json()["session_id"]
        assert s1 != s2

        # реальный SessionManager: профиль доехал до листинга (регрессия profile=<default>)
        by_id = {e["session_id"]: e for e in (await client.get("/sessions")).json()}
        assert by_id[s1]["profile"] == "standalone"
        assert by_id[s2]["profile"] == "standalone"
        assert (tmp_path / s1 / "workspace").is_dir()
        assert (tmp_path / s2 / "workspace").is_dir()

        # ход в s1 (висит на fake-агенте) — busy только у неё
        r = await client.post(f"/sessions/{s1}/turn", json={"text": "go"})
        assert r.status_code == 202
        listing = {e["session_id"]: e for e in (await client.get("/sessions")).json()}
        assert listing[s1]["is_busy"] is True
        assert listing[s2]["is_busy"] is False

        # второй ход в busy-сессию → 409; в свободную → 202 (независимость сессий)
        assert (await client.post(f"/sessions/{s1}/turn", json={"text": "x"})).status_code == 409
        assert (await client.post(f"/sessions/{s2}/turn", json={"text": "y"})).status_code == 202

        # delete busy-сессии: ход гасится, диск чистится, вторая живёт
        assert (await client.delete(f"/sessions/{s1}")).status_code == 204
        assert not (tmp_path / s1).exists()
        health = (await client.get("/health")).json()
        assert health["sessions_count"] == 1

        # прибрать висящий ход s2
        assert (await client.post(f"/sessions/{s2}/stop")).json()["cancelled"] is True
        await app_wait_idle(client, s2)


async def app_wait_idle(client, session_id):
    """Дождаться завершения Task хода после stop (иначе pending-task warning)."""
    manager = client._transport.app.state.sessions
    session = manager.get_session(session_id)
    if session is not None:
        await session.wait_turn()


# --- SSE-хелперы -----------------------------------------------------------------------

def _frame_data(frame: str) -> dict:
    """Распарсить SSE-кадр: JSON после 'data: '."""
    payload = frame.split("data: ", 1)[1].rstrip("\n")
    return json.loads(payload)


def test_sse_frame_messages_normalized():
    """`messages`-кадр — чистый JSON: текст-дельта + reasoning-дельта, не repr-строка."""
    chunk = AIMessageChunk(
        content=[
            {
                "type": "reasoning",
                "content": [{"index": 0, "type": "reasoning_text", "text": "мысль"}],
                "index": 0,
            },
            {"type": "text", "text": "Прив", "index": 1},
        ],
        response_metadata={"model_provider": "openai"},
    )
    metadata = {"langgraph_node": "model", "not_json": object()}
    frame = _sse_frame(StreamEvent("main", "messages", (chunk, metadata)))

    data = _frame_data(frame)["data"]
    assert data["text"] == "Прив"
    assert data["reasoning"] == "мысль"
    assert data["langgraph_node"] == "model"
    assert "not_json" not in json.dumps(data)  # из metadata тащим только полезное


def test_sse_frame_messages_tool_call_chunks():
    chunk = AIMessageChunk(
        content=[],
        tool_call_chunks=[
            {"type": "tool_call_chunk", "name": "json_patch", "args": '{"op', "id": "c1", "index": 0}
        ],
        response_metadata={"model_provider": "openai"},
    )
    frame = _sse_frame(StreamEvent("main", "messages", (chunk, {})))

    data = _frame_data(frame)["data"]
    assert data["text"] == ""
    assert data["reasoning"] is None
    (tc,) = data["tool_call_chunks"]
    assert tc["name"] == "json_patch"
    assert tc["args"] == '{"op'


def test_sse_frame_messages_non_ai_chunk():
    """LangGraph в mode=messages эмитит и ToolMessage — кадр остаётся JSON-чистым."""
    tool = ToolMessage(content="ok", tool_call_id="c1", name="read_file", status="success")
    frame = _sse_frame(StreamEvent("main", "messages", (tool, {"langgraph_node": "tools"})))

    data = _frame_data(frame)["data"]
    assert data["message"]["type"] == "tool"
    assert data["message"]["data"]["content"] == "ok"
    assert data["langgraph_node"] == "tools"
    assert "text" not in data  # токен-контракт — только для чанков модели


def test_sse_frame_updates_messages_are_structured():
    """`updates`-кадр: tool_calls/статус — структуры, а не Python-repr."""
    ai = AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {"path": "x"}, "id": "c1", "type": "tool_call"}],
    )
    tool = ToolMessage(content="ok", tool_call_id="c1", name="read_file", status="success")
    event = StreamEvent(
        "main", "updates", {"model": {"messages": [ai]}, "tools": {"messages": [tool]}}
    )

    data = _frame_data(_sse_frame(event))["data"]
    model_msg = data["model"]["messages"][0]
    assert model_msg["type"] == "ai"
    (tc,) = model_msg["data"]["tool_calls"]
    assert tc["name"] == "read_file"
    assert tc["args"] == {"path": "x"}
    tool_msg = data["tools"]["messages"][0]
    assert tool_msg["type"] == "tool"
    assert tool_msg["data"]["status"] == "success"
    assert tool_msg["data"]["content"] == "ok"


def test_sse_frame_custom_control_passthrough():
    """`custom`/`control` уже JSON-чисты — проходят без изменений."""
    for mode, payload in [
        ("custom", {"type": "hitl_question", "hitl_id": "h1"}),
        ("control", {"type": "turn_done", "turn_id": "turn-1"}),
    ]:
        data = _frame_data(_sse_frame(StreamEvent("main", mode, payload)))
        assert data["data"] == payload


def test_sse_frame_subagent_event_messages_normalized():
    """вложенный `subagent_event` c mode=messages несёт сырой
    `(AIMessageChunk, metadata)` — нормализуется рекурсивно, а не через default=str."""
    chunk = AIMessageChunk(
        content=[{"type": "text", "text": "суб-токен", "index": 0}],
        response_metadata={"model_provider": "openai"},
    )
    payload = {
        "type": "subagent_event",
        "namespace": "subagent.default.abc123",
        "mode": "messages",
        "data": (chunk, {"langgraph_node": "model"}),
    }
    data = _frame_data(_sse_frame(StreamEvent("main", "custom", payload)))["data"]
    assert data["type"] == "subagent_event"
    assert data["namespace"] == "subagent.default.abc123"
    assert data["mode"] == "messages"
    # вложенный data — нормализованный {text, reasoning}, не repr-строка
    assert data["data"]["text"] == "суб-токен"
    assert data["data"]["langgraph_node"] == "model"


def test_sse_frame_subagent_event_updates_normalized():
    """вложенный `subagent_event` c mode=updates несёт LangChain-сообщения —
    структура `{node: {messages: [{type, data}]}}` восстанавливается для дорожки субагента."""
    ai = AIMessage(
        content="",
        tool_calls=[{"name": "read_file", "args": {"path": "x"}, "id": "c1", "type": "tool_call"}],
    )
    payload = {
        "type": "subagent_event",
        "namespace": "subagent.default.abc123",
        "mode": "updates",
        "data": {"model": {"messages": [ai]}},
    }
    data = _frame_data(_sse_frame(StreamEvent("main", "custom", payload)))["data"]
    model_msg = data["data"]["model"]["messages"][0]
    assert model_msg["type"] == "ai"
    (tc,) = model_msg["data"]["tool_calls"]
    assert tc["name"] == "read_file"


def test_sse_frame_subagent_event_other_type_passthrough():
    """`custom`-кадры без subagent_event (namespace_*, document_patch, HITL) — как есть."""
    payload = {"type": "namespace_open", "namespace": "subagent.default.abc123"}
    data = _frame_data(_sse_frame(StreamEvent("main", "custom", payload)))["data"]
    assert data == payload


def test_sse_frame_malformed_messages_does_not_raise():
    """Сбой нормализации не роняет стрим: деградация default=str."""
    frame = _sse_frame(StreamEvent("main", "messages", {"weird": object()}))
    assert frame.startswith("event: messages\n")
    _frame_data(frame)  # кадр всё равно валидный JSON (default=str)


# --- fs / checkpoints -------

@pytest.fixture
def client_dev_endpoints(tmp_path):
    """Клиент для маршрутов file-viewer'а/чекпоинтов (регистрируются всегда)."""
    app = create_app()
    manager = FakeSessionManager()
    session = FakeSession()
    manager.sessions["s1"] = session
    app.state.sessions = manager
    app.state.session_root = tmp_path
    (tmp_path / "s1" / "workspace").mkdir(parents=True)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), session


async def test_fs_tree_lists_workspace_files(client_dev_endpoints, tmp_path):
    client, _ = client_dev_endpoints
    doc = tmp_path / "s1" / "workspace" / "notes.txt"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("hello", encoding="utf-8")

    async with client:
        r = await client.get("/sessions/s1/fs/tree")
    assert r.status_code == 200
    tree = r.json()
    ws = next(n for n in tree if n["name"] == "workspace")
    names = [c["name"] for c in ws["children"]]
    assert "notes.txt" in names
    assert all(n["name"] != "checkpoints" for n in tree)


async def test_fs_tree_after_write(client_dev_endpoints, tmp_path):
    client, _ = client_dev_endpoints
    doc = tmp_path / "s1" / "workspace" / "doc.json"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("{}", encoding="utf-8")
    async with client:
        r = await client.get("/sessions/s1/fs/tree")
    assert r.status_code == 200
    ws = next(n for n in r.json() if n["name"] == "workspace")
    assert any(c["name"] == "doc.json" for c in ws["children"])


async def test_fs_file_reads_text(client_dev_endpoints, tmp_path):
    client, _ = client_dev_endpoints
    target = tmp_path / "s1" / "workspace" / "readme.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("# hi", encoding="utf-8")

    async with client:
        r = await client.get(
            "/sessions/s1/fs/file",
            params={"path": "workspace/readme.md"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "# hi"
    assert body["size"] == 4


async def test_fs_file_allows_runtime(client_dev_endpoints, tmp_path):
    client, _ = client_dev_endpoints
    runtime = tmp_path / "s1" / "workspace" / ".runtime" / "messages.jsonl"
    runtime.parent.mkdir(parents=True, exist_ok=True)
    runtime.write_text('{"x":1}\n', encoding="utf-8")

    async with client:
        r = await client.get(
            "/sessions/s1/fs/file",
            params={"path": "workspace/.runtime/messages.jsonl"},
        )
    assert r.status_code == 200
    assert "x" in r.json()["content"]


async def test_fs_file_path_traversal_blocked(client_dev_endpoints, tmp_path):
    client, _ = client_dev_endpoints
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    async with client:
        r = await client.get(
            "/sessions/s1/fs/file",
            params={"path": "../outside.txt"},
        )
    assert r.status_code in (400, 403, 404)
    assert r.status_code != 200


async def test_fs_file_binary_415(client_dev_endpoints, tmp_path):
    client, _ = client_dev_endpoints
    target = tmp_path / "s1" / "workspace" / "blob.bin"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\xff\xfe")

    async with client:
        r = await client.get(
            "/sessions/s1/fs/file",
            params={"path": "workspace/blob.bin"},
        )
    assert r.status_code == 415


async def test_fs_file_oversize_413(client_dev_endpoints, tmp_path, monkeypatch):
    """Файл больше лимита → 413. Лимит понижаем monkeypatch'ем ради скорости."""
    client, _ = client_dev_endpoints
    monkeypatch.setattr(dev_routes, "_MAX_DEV_FILE_BYTES", 8)
    target = tmp_path / "s1" / "workspace" / "big.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("0123456789", encoding="utf-8")  # 10 байт > 8

    async with client:
        r = await client.get(
            "/sessions/s1/fs/file",
            params={"path": "workspace/big.txt"},
        )
    assert r.status_code == 413


async def test_checkpoints_list(client_dev_endpoints, tmp_path):
    client, _ = client_dev_endpoints
    cp_dir = tmp_path / "s1" / "checkpoints" / "c000_init"
    cp_dir.mkdir(parents=True)
    (cp_dir / "doc.json").write_text("{}", encoding="utf-8")
    async with client:
        r = await client.get("/sessions/s1/checkpoints")
    assert r.status_code == 200
    cps = r.json()
    assert len(cps) >= 1
    assert cps[0]["id"].startswith("c000")
    assert "mtime" in cps[0]
    assert "label" in cps[0]


async def test_checkpoint_restore_busy_409(client_dev_endpoints, tmp_path):
    client, session = client_dev_endpoints
    workspace = tmp_path / "s1" / "workspace"
    checkpoints = tmp_path / "s1" / "checkpoints"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "doc.json").write_text("{}", encoding="utf-8")
    cp_dir = checkpoints / "c000_init"
    cp_dir.mkdir(parents=True)
    (cp_dir / "doc.json").write_text('{"restored":true}', encoding="utf-8")

    session.is_busy = True
    async with client:
        r = await client.post("/sessions/s1/checkpoints/c000_init/restore")
    assert r.status_code == 409


async def test_checkpoint_restore_ok(client_dev_endpoints, tmp_path):
    client, session = client_dev_endpoints
    workspace = tmp_path / "s1" / "workspace"
    checkpoints = tmp_path / "s1" / "checkpoints"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "doc.json").write_text('{"v":"live"}', encoding="utf-8")
    cp_dir = checkpoints / "c000_init"
    cp_dir.mkdir(parents=True)
    (cp_dir / "doc.json").write_text('{"v":"snap"}', encoding="utf-8")

    session.is_busy = False
    async with client:
        r = await client.post("/sessions/s1/checkpoints/c000_init/restore")
    assert r.status_code == 200
    assert r.json() == {"restored": "c000_init"}
    assert json.loads((workspace / "doc.json").read_text(encoding="utf-8")) == {"v": "snap"}


async def test_checkpoint_restore_reloads_agent_history(client_dev_endpoints, tmp_path):
    """После успешного restore живая история агента ре-гидратируется."""
    client, session = client_dev_endpoints
    workspace = tmp_path / "s1" / "workspace"
    checkpoints = tmp_path / "s1" / "checkpoints"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "doc.json").write_text('{"v":"live"}', encoding="utf-8")
    cp_dir = checkpoints / "c000_init"
    cp_dir.mkdir(parents=True)
    (cp_dir / "doc.json").write_text('{"v":"snap"}', encoding="utf-8")

    session.is_busy = False
    async with client:
        r = await client.post("/sessions/s1/checkpoints/c000_init/restore")
    assert r.status_code == 200
    assert session._agent.reload_calls == 1


async def test_checkpoint_restore_invalid_id(client_dev_endpoints):
    """Некорректный cp_id (malformed / '..') → 400, до любой ФС-операции."""
    client, session = client_dev_endpoints
    session.is_busy = False
    async with client:
        r_malformed = await client.post("/sessions/s1/checkpoints/notacheckpoint/restore")
        # `..` в имени (одиночный сегмент, не '/../' — клиент не нормализует его в путь) →
        # ветка запрета traversal в _validate_checkpoint_id.
        r_dotdot = await client.post("/sessions/s1/checkpoints/c001_../restore")
    assert r_malformed.status_code == 400
    assert r_dotdot.status_code == 400
    assert session._agent.reload_calls == 0


# --- статика дев-фронта ------------------------------------------------------------------

async def test_static_index_served_at_root(client_and_session):
    client, _ = client_and_session
    async with client:
        r = await client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")


async def test_static_mount_does_not_shadow_api(client_and_session):
    """API-роуты зарегистрированы до mount — берут приоритет над статикой."""
    client, _ = client_and_session
    async with client:
        r = await client.get("/sessions")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


async def test_sse_stream_serializes_event():
    session = FakeSession()
    await session._events.put(
        StreamEvent("main", "custom", {"type": "hitl_question", "hitl_id": "x"})
    )
    stream = _sse_stream(session)
    frame = await asyncio.wait_for(anext(stream), timeout=1.0)
    await stream.aclose()

    assert frame.startswith("event: custom\n")
    assert "hitl_question" in frame
    assert frame.endswith("\n\n")


async def test_sse_stream_heartbeat_on_idle(monkeypatch):
    monkeypatch.setattr(sse, "_SSE_HEARTBEAT_INTERVAL", 0.01)
    session = FakeSession()  # очередь пуста → таймаут → heartbeat
    stream = _sse_stream(session)
    frame = await asyncio.wait_for(anext(stream), timeout=1.0)
    await stream.aclose()
    assert frame == ": ping\n\n"


async def test_sse_stream_survives_heartbeat_then_delivers(monkeypatch):
    """Регрессия (обкатка 15.5): heartbeat не должен убивать генератор событий.

    Старый код через wait_for(anext) отменял anext на таймауте → CancelledError убивал
    events(), и следующий anext ронял стрим StopAsyncIteration'ом. Проверяем полный цикл:
    ping на паузе → событие ПОСЛЕ ping'а доставляется, стрим жив.
    """
    monkeypatch.setattr(sse, "_SSE_HEARTBEAT_INTERVAL", 0.01)
    session = FakeSession()
    stream = _sse_stream(session)

    frame = await asyncio.wait_for(anext(stream), timeout=1.0)
    assert frame == ": ping\n\n"  # пауза → heartbeat

    await session._events.put(StreamEvent("main", "custom", {"type": "x"}))
    frame2 = await asyncio.wait_for(anext(stream), timeout=1.0)
    await stream.aclose()
    assert frame2.startswith("event: custom\n")
