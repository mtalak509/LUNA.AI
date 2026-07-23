"""ToolErrorMiddleware - перехватывает tool errors и отдает их агенту как ToolMessage(status=error)"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException

_logger = logging.getLogger(__name__)


class ToolErrorMiddleware(AgentMiddleware):
    """Перехватывает tool errors и отдает их агенту как ToolMessage(status=error).

    create_agent строит ToolNode с дефолтным handle_tool_errors, который модели возвращает
    только ToolInvocationError (валидация входа), а прочее перевыбрасывает — и ход падает.
    Здесь восстанавливаем контракт «ошибка тула видна агенту, он корректируется». Должен быть
    ПЕРВЫМ в стеке middleware → outermost в tool-call цепочке (factory: first=outermost).

    Различаем ожидаемый отказ тула (ToolException — доменная ошибка, штатный исход) и баг:
    первое логируем тихо без traceback, второе — warning с traceback.
    """

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage]],
    ) -> ToolMessage:
        try:
            return await handler(request)
        except Exception as e:
            name = request.tool_call['name']
            if isinstance(e, ToolException):
                _logger.debug("tool %s rejected: %s", name, e)
            else:
                _logger.warning("tool %s raised %s", name, type(e).__name__, exc_info=True)
            return ToolMessage(
                content=f"Tool error: {name} raised {type(e).__name__}: {e}",
                tool_call_id=request.tool_call["id"],
                name=name,
                status="error",
            )