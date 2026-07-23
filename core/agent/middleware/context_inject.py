"""ContextInjectMiddleware — подмешивает рабочий контекст MainAgent в конец истории сообщений.

Универсальный механизм: один middleware + список провайдеров. Провайдер = источник
одного блока контекста. Добавить источник = добавить провайдер, не трогая стек middleware.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Protocol

from langchain.agents.middleware import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import HumanMessage


# --- Providers --------------------------------------------------------------------------------

class ContextProvider(Protocol):
    """Источник одного блока контекста.

    `tag` — имя XML-тега-секции (англ. snake_case), которым middleware оборачивает блок.
    Формат держим единым с agent.md (секции = XML-теги, НЕ markdown `#`): итоговый
    системный промпт — один документ, базовая часть (agent.md) тоже на тегах.
    """

    tag: str

    async def block(self, ctx) -> str | None:
        """Вернуть текст блока или None/'' — если инжектить нечего."""

class FileContextProvider(ContextProvider):
    """notes/decisions.md из рабочей зоны MainAgent (append-only журнал решений)."""

    tag = "project_decisions"

    async def block(self, ctx) -> str | None:
        path = Path(ctx.workspace_path) / "notes" / "decisions.md"
        try:
            return path.read_text(encoding="utf-8").strip() or None
        except (FileNotFoundError, OSError):
            return None


# --- Middleware -------------------------------------------------------------------------------

_ENVLOPE_TAG = "working_context"
_ENVLOPE_MESSAGE = (
    "Ниже приведён автоматически сформированный рабочий контекст, актуальный "
    "на момент текущего вызова модели. Это не реплика пользователя и не отдельное "
    "задание: не отвечай на него. Используй вложенные секции как источник данных "
    "о текущем состоянии сессии. При расхождении с устаревшими сведениями из истории "
    "диалога опирайся на этот контекст. Системные инструкции и текущий запрос "
    "пользователя сохраняют приоритет."
)


class ContextInjectMiddleware(AgentMiddleware):
    """Подмешивает рабочий контекст MainAgent в системный промпт."""

    def __init__(self, providers: list[ContextProvider] | None = None) -> None:
        self.providers = providers if providers is not None else [
            FileContextProvider(),
        ]

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]]
    ) -> ModelResponse:
        agent_context = request.runtime.context
        if agent_context.agent_scope != "main":
            return await handler(request)

        sections = []
        for provider in self.providers:
            text = await provider.block(agent_context)
            if text is not None:
                # Секция = XML-тег snake_case (формат как в agent.md).
                sections.append(f"<{provider.tag}>\n{text}\n</{provider.tag}>")

        if sections:
            body = "\n\n".join(sections)
            ctx_message = HumanMessage(
                content=f"<{_ENVLOPE_TAG}>\n{_ENVLOPE_MESSAGE}\n\n{body}\n</{_ENVLOPE_TAG}>"
            )
            request = request.override(messages=request.messages + [ctx_message])

        return await handler(request)
    
