"""
Unit-тесты `PermissionMiddleware` (2.3) в изоляции.

Middleware трогает у запроса только `runtime.context.permission_mode`, `tools` и
`override(tools=...)`, поэтому реальный `ModelRequest` не нужен — подменяем лёгким фейком,
повторяющим этот контракт. `handler` — фейковый async-колбэк, запоминающий, с каким пулом
вызов дошёл «до модели». Тулы настоящие (через `agent_tool`), чтобы `is_write_tool` читал
реальную `metadata`.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.agent.middleware.permission import PermissionMiddleware
from core.agent.tools import agent_tool


# --- настоящие тулы с атрибутами (для is_write_tool) --------------------------------


@agent_tool(is_write=True)
def write_tool() -> str:
    """Пишущий тул."""
    return "w"


@agent_tool
def read_tool() -> str:
    """Читающий тул (is_write по умолчанию False)."""
    return "r"


# --- фейковый ModelRequest: ровно тот контракт, что использует middleware ------------


@dataclass
class _FakeCtx:
    permission_mode: str


@dataclass
class _FakeRuntime:
    context: _FakeCtx


class _FakeModelRequest:
    def __init__(self, tools, permission_mode: str) -> None:
        self.tools = tools
        self.runtime = _FakeRuntime(_FakeCtx(permission_mode))

    def override(self, *, tools):
        """Как настоящий ModelRequest: возвращает НОВЫЙ запрос с заменённым пулом."""
        return _FakeModelRequest(tools, self.runtime.context.permission_mode)


def _make_handler():
    """Фейковый async-handler: запоминает дошедший запрос, возвращает маркер ответа."""
    captured: dict = {}

    async def handler(request):
        captured["request"] = request
        return "MODEL_RESPONSE"

    return handler, captured


# --- фейковый ToolCallRequest: контракт, что использует awrap_tool_call --------------
# Гейт читает у запроса только `runtime.context.permission_mode`, `tool` (BaseTool|None)
# и `tool_call` (dict с name/id). `_FakeRuntime`/`_FakeCtx` переиспользуем сверху.


class _FakeToolCallRequest:
    def __init__(self, tool, permission_mode: str) -> None:
        self.tool = tool
        self.tool_call = {"name": getattr(tool, "name", "?"), "args": {}, "id": "call-1"}
        self.runtime = _FakeRuntime(_FakeCtx(permission_mode))


def _make_tool_handler():
    """Фейковый async tool-handler: помечает факт вызова, возвращает маркер исполнения."""
    captured: dict = {"called": False}

    async def handler(request):
        captured["called"] = True
        return "TOOL_EXECUTED"

    return handler, captured


# --- тесты --------------------------------------------------------------------------


async def test_plan_filters_out_write_tools() -> None:
    """В режиме plan write-тул не доходит до модели, read-тул остаётся."""
    mw = PermissionMiddleware()
    handler, captured = _make_handler()
    request = _FakeModelRequest(tools=[read_tool, write_tool], permission_mode="plan")

    result = await mw.awrap_model_call(request, handler)

    assert result == "MODEL_RESPONSE"  # handler вызван, ответ проброшен
    passed_tools = captured["request"].tools
    assert read_tool in passed_tools
    assert write_tool not in passed_tools


async def test_act_keeps_all_tools() -> None:
    """В режиме act пул не урезается — модель видит и write-, и read-тулы."""
    mw = PermissionMiddleware()
    handler, captured = _make_handler()
    request = _FakeModelRequest(tools=[read_tool, write_tool], permission_mode="act")

    await mw.awrap_model_call(request, handler)

    assert captured["request"].tools == [read_tool, write_tool]


async def test_plan_with_only_read_tools_passes_through() -> None:
    """Plan без write-тулов: пул не меняется (нечего вычитать)."""
    mw = PermissionMiddleware()
    handler, captured = _make_handler()
    request = _FakeModelRequest(tools=[read_tool], permission_mode="plan")

    await mw.awrap_model_call(request, handler)

    assert captured["request"].tools == [read_tool]


# --- гейт исполнения (awrap_tool_call) ----------------------------------------------
# Жёсткая граница: даже если модель эмитнула write-вызов в Plan (hide — лишь подсказка),
# ToolNode его не исполняет. Гейт коротит вызов в ToolMessage(status="error").


async def test_plan_blocks_write_tool_execution() -> None:
    """Plan + write-тул: handler НЕ вызван, наружу — ToolMessage(status='error')."""
    mw = PermissionMiddleware()
    handler, captured = _make_tool_handler()
    request = _FakeToolCallRequest(tool=write_tool, permission_mode="plan")

    result = await mw.awrap_tool_call(request, handler)

    assert captured["called"] is False  # тул не исполнен
    assert result.status == "error"
    assert result.tool_call_id == "call-1"
    assert result.name == write_tool.name


async def test_plan_allows_read_tool_execution() -> None:
    """Plan + read-тул: гейт пропускает, handler исполняет тул."""
    mw = PermissionMiddleware()
    handler, captured = _make_tool_handler()
    request = _FakeToolCallRequest(tool=read_tool, permission_mode="plan")

    result = await mw.awrap_tool_call(request, handler)

    assert captured["called"] is True
    assert result == "TOOL_EXECUTED"


async def test_act_allows_write_tool_execution() -> None:
    """Act + write-тул: гейт не вмешивается, тул исполняется."""
    mw = PermissionMiddleware()
    handler, captured = _make_tool_handler()
    request = _FakeToolCallRequest(tool=write_tool, permission_mode="act")

    result = await mw.awrap_tool_call(request, handler)

    assert captured["called"] is True
    assert result == "TOOL_EXECUTED"
