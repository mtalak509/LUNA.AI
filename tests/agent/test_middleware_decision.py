"""
Unit-тесты `DecisionMiddleware` (2.3 → фикс HITL) в изоляции.

Гейт теперь программный: любой write-тул (по `is_write_tool`) в режиме `confirm`
паркуется на `request_human`, не завися от того, вызовет ли модель отдельный тул
подтверждения (`confirm_with_user` упразднён — модель его игнорировала, что делало
gate ненадёжным). Реальный `ToolCallRequest`/`BaseTool` не нужны — подменяем лёгкими
фейками, а `request_human` мокаем прямо в модуле `decision.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from core.agent.middleware.decision import DecisionMiddleware


# --- фейковые тул/контекст/реквест: ровно тот контракт, что использует middleware ---


@dataclass
class _FakeTool:
    metadata: dict = field(default_factory=dict)


@dataclass
class _FakeCtx:
    decision_mode: str
    namespace: str = "main"
    workspace_path: Any = None
    agent_scope: str = "main"
    session_id: str = "s1"


@dataclass
class _FakeRuntime:
    context: _FakeCtx


@dataclass
class _FakeToolCallRequest:
    tool_call: dict
    runtime: _FakeRuntime
    tool: _FakeTool | None


def _req(
    name: str, decision_mode: str, *, is_write: bool, args: dict | None = None, tc_id: str = "call_1"
) -> _FakeToolCallRequest:
    return _FakeToolCallRequest(
        tool_call={"name": name, "id": tc_id, "args": args or {}},
        runtime=_FakeRuntime(_FakeCtx(decision_mode)),
        tool=_FakeTool(metadata={"is_write": is_write}),
    )


def _make_handler(return_value):
    """Фейковый async-handler: фиксирует факт вызова, возвращает заданный результат."""
    captured = {"called": False}

    async def handler(request):
        captured["called"] = True
        captured["request"] = request
        return return_value

    return handler, captured


@pytest.fixture(autouse=True)
def _no_real_hitl(monkeypatch):
    """По умолчанию request_human не должен звать реальный реестр — тесты его мокают явно."""
    async def _boom(*args, **kwargs):
        raise AssertionError("request_human не должен вызываться в этой ветке")

    monkeypatch.setattr(
        "core.agent.middleware.decision.request_human", _boom
    )


# --- тесты --------------------------------------------------------------------------


async def test_read_tool_passes_through_even_in_confirm() -> None:
    """Не-write тул исполняется без гейта в любом режиме."""
    mw = DecisionMiddleware()
    sentinel = ToolMessage(content="HANDLER", tool_call_id="call_1")
    handler, captured = _make_handler(sentinel)

    result = await mw.awrap_tool_call(_req("read_file", "confirm", is_write=False), handler)

    assert captured["called"] is True
    assert result is sentinel


async def test_write_tool_accept_all_passes_through_without_confirm() -> None:
    """accept_all: write-тул исполняется сразу, request_human не вызывается."""
    mw = DecisionMiddleware()
    sentinel = ToolMessage(content="HANDLER", tool_call_id="call_1")
    handler, captured = _make_handler(sentinel)

    result = await mw.awrap_tool_call(
        _req("write_file", "accept_all", is_write=True), handler
    )

    assert captured["called"] is True
    assert result is sentinel


async def test_write_tool_confirm_mode_approved_calls_handler(monkeypatch) -> None:
    """confirm + пользователь подтвердил (True) → handler вызывается."""
    mw = DecisionMiddleware()
    sentinel = ToolMessage(content="HANDLER", tool_call_id="call_1")
    handler, captured = _make_handler(sentinel)

    called_with = {}

    async def fake_request_human(kind, payload, **kwargs):
        called_with["kind"] = kind
        called_with["payload"] = payload
        called_with.update(kwargs)
        return True

    monkeypatch.setattr(
        "core.agent.middleware.decision.request_human", fake_request_human
    )

    result = await mw.awrap_tool_call(
        _req("json_patch", "confirm", is_write=True, args={"ops": [1]}), handler
    )

    assert captured["called"] is True
    assert result is sentinel
    assert called_with["kind"] == "confirm"
    assert "json_patch" in called_with["payload"]["action_description"]
    assert "{'ops': [1]}" in called_with["payload"]["action_description"]


async def test_write_tool_confirm_mode_rejected_returns_error(monkeypatch) -> None:
    """confirm + пользователь отклонил (False) → handler НЕ вызывается, ToolMessage(error)."""
    mw = DecisionMiddleware()
    handler, captured = _make_handler(ToolMessage(content="HANDLER", tool_call_id="call_1"))

    async def fake_request_human(kind, payload, **kwargs):
        return False

    monkeypatch.setattr(
        "core.agent.middleware.decision.request_human", fake_request_human
    )

    result = await mw.awrap_tool_call(
        _req("delete_file", "confirm", is_write=True, tc_id="call_9"), handler
    )

    assert captured["called"] is False
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call_9"
    assert result.name == "delete_file"
