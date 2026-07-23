"""PermissionMidleware - фильтрует пул тулов в зависимости от PermissionMode (Plan/Act)"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse, ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from core.agent.tools import is_write_tool


class PermissionMiddleware(AgentMiddleware):
    """В режиме `plan` запрещает write-тулы двумя слоями, оба per-turn (режим меняется от
    хода к ходу, а граф строится однократно — фильтровать в сборке пула нельзя).

    - `awrap_model_call` (hide, мягкий): вычитает write-тулы из того, что видит МОДЕЛЬ на
      границе вызова — экономит контекст и не провоцирует модель звать недоступное.
    - `awrap_tool_call` (gate, жёсткий): граница исполнения. Hide — лишь подсказка модели;
      модель вероятностна и может эмитнуть вызов write-тула всё равно (тем более если имя
      есть в системном промпте), а `ToolNode` собран из полного пула и исполнил бы его.
      Гейт коротит такой вызов в `ToolMessage(status="error")` (self-correction).

    Оба слоя единообразны для main и субагентов; зависят только от атрибута `is_write`.
    """

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if request.runtime.context.permission_mode == "plan":
            filtred = [tool for tool in request.tools if not is_write_tool(tool)]
            request = request.override(tools=filtred)
        return await handler(request)

    async def awrap_tool_call(
        self, 
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        if (
            request.runtime.context.permission_mode == "plan" and
            request.tool is not None and
            is_write_tool(request.tool)
        ):
            return ToolMessage(
                  content=(
                      f"Инструмент '{request.tool_call['name']}' недоступен в режиме Plan "
                      "(только чтение). Вызов не выполнен."
                  ),
                  tool_call_id=request.tool_call["id"],
                  name=request.tool_call["name"],
                  status="error",
              )
        return await handler(request)