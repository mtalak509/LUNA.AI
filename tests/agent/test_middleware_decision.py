"""
Unit tests for `DecisionMiddleware` in isolation.

The gate is programmatic: any write tool (by `is_write_tool`) in `confirm` mode parks on
`request_human`, regardless of whether the model calls a separate confirmation tool (there
is none: a model-driven confirmation tool was ignored by the model, which made the gate
unreliable). Real `ToolCallRequest`/`BaseTool` are not needed — light fakes stand in, and
`request_human` is mocked right in the `decision.py` module.
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
    """Fake async handler: records that it was called, returns the given result."""
    captured = {"called": False}

    async def handler(request):
        captured["called"] = True
        captured["request"] = request
        return return_value

    return handler, captured


@pytest.fixture(autouse=True)
def _no_real_hitl(monkeypatch):
    """By default request_human must not reach the real registry — tests mock it explicitly."""
    async def _boom(*args, **kwargs):
        raise AssertionError("request_human не должен вызываться в этой ветке")

    monkeypatch.setattr(
        "core.agent.middleware.decision.request_human", _boom
    )


# --- тесты --------------------------------------------------------------------------


async def test_read_tool_passes_through_even_in_confirm() -> None:
    """A non-write tool runs without the gate in any mode."""
    mw = DecisionMiddleware()
    sentinel = ToolMessage(content="HANDLER", tool_call_id="call_1")
    handler, captured = _make_handler(sentinel)

    result = await mw.awrap_tool_call(_req("read", "confirm", is_write=False), handler)

    assert captured["called"] is True
    assert result is sentinel


async def test_write_tool_accept_all_passes_through_without_confirm() -> None:
    """accept_all: a write tool runs at once, request_human is not called."""
    mw = DecisionMiddleware()
    sentinel = ToolMessage(content="HANDLER", tool_call_id="call_1")
    handler, captured = _make_handler(sentinel)

    result = await mw.awrap_tool_call(
        _req("write", "accept_all", is_write=True), handler
    )

    assert captured["called"] is True
    assert result is sentinel


async def test_write_tool_confirm_mode_approved_calls_handler(monkeypatch) -> None:
    """confirm + the user approved (True) → the handler is called."""
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
        _req("edit", "confirm", is_write=True, args={"ops": [1]}), handler
    )

    assert captured["called"] is True
    assert result is sentinel
    assert called_with["kind"] == "confirm"
    assert "edit" in called_with["payload"]["action_description"]
    assert "{'ops': [1]}" in called_with["payload"]["action_description"]


async def test_write_tool_confirm_mode_rejected_returns_error(monkeypatch) -> None:
    """confirm + the user rejected (False) → the handler is NOT called, ToolMessage(error)."""
    mw = DecisionMiddleware()
    handler, captured = _make_handler(ToolMessage(content="HANDLER", tool_call_id="call_1"))

    async def fake_request_human(kind, payload, **kwargs):
        return False

    monkeypatch.setattr(
        "core.agent.middleware.decision.request_human", fake_request_human
    )

    result = await mw.awrap_tool_call(
        _req("rm", "confirm", is_write=True, tc_id="call_9"), handler
    )

    assert captured["called"] is False
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert result.tool_call_id == "call_9"
    assert result.name == "rm"
