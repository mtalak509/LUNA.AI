"""DecisionMiddleware — gate на подтверждения (Decision Mode: confirm / accept_all).

Гейтит выполнение write-тулов напрямую (по `is_write_tool`), не полагаясь на то, что
модель сама позовёт отдельный тул-подтверждение — она может его игнорировать, что делает
такой gate ненадёжным как механизм безопасности. `confirm_with_user` как отдельный тул
упразднён; описание действия для пользователя собирается из имени тула и аргументов.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage

from core.agent.tools import is_write_tool
from core.agent.tools._hitl import request_human


class DecisionMiddleware(AgentMiddleware):
    """В режиме `confirm` перед исполнением любого write-тула паркует ход на подтверждении
    пользователя; в `accept_all` write-тулы исполняются без вопроса. Не-write тулы проходят
    насквозь в обоих режимах.
    """

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage]]
    ) -> ToolMessage:
        if request.tool is None or not is_write_tool(request.tool):
            return await handler(request)

        if request.runtime.context.decision_mode == "accept_all":
            return await handler(request)

        ctx = request.runtime.context
        action_description = (
            f"Агент хочет вызвать инструмент: {request.tool_call['name']} "
            f"с аргументами {request.tool_call.get('args')}"
        )
        confirmed = await request_human(
            kind="confirm",
            payload={"action_description": action_description},
            namespace=ctx.namespace,
            session_id=ctx.session_id,
            workspace_path=ctx.workspace_path,
            write_marker=(ctx.agent_scope == "main"),
        )
        if not confirmed:
            return ToolMessage(
                content="Пользователь отклонил выполнение действия.",
                tool_call_id=request.tool_call["id"],
                name=request.tool_call["name"],
                status="error",
            )
        return await handler(request)
