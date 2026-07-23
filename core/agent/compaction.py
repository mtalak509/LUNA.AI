"""HistoryCompactor — компакция рабочей истории MainAgent (сжать один раз, персистить).

Своя компакция вместо встроенного SummarizationMiddleware: историю держит BaseAgent
(`self._messages` + `messages.jsonl`), поэтому сжимаем её на границе хода (в `run_stream`),
а не внутри графа. Summarizer инжектируется — класс не зависит от LLM/конфига (тестируется
без сети) и не тянет `base.py` (нет цикла импортов).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain_core.messages import BaseMessage, HumanMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from core.agent.messages import MessageStore

# Callable: сжать переданные сообщения в текст сводки (в проде — один LLM-вызов).
Summarizer = Callable[[list[BaseMessage]], Awaitable[str]]


class HistoryCompactor:
    """Сжимает старую часть истории, когда превышен порог токенов; персистит саммари.

    Модель Claude Code: сжать ОДИН раз, дальше использовать готовое саммари. Рабочая копия
    становится `[summary, *tail]`; в `messages.jsonl` уходит маркер-саммари + указатель `head`
    (через `MessageStore.append_summary`), сырой лог сохраняется целиком (append-only).
    """

    def __init__(
        self,
        store: MessageStore,
        summarizer: Summarizer,
        *,
        trigger_tokens: int = 70_000,
        keep_messages: int = 30,         # ≈ последние 2-3 содержательных хода
    ) -> None:
        self._store = store
        self._summarize = summarizer
        self._trigger_tokens = trigger_tokens
        self._keep_messages = keep_messages

    async def maybe_compact(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        """Вернуть сжатую историю `[summary, *tail]` или исходную, если порог не достигнут."""
        if count_tokens_approximately(messages) < self._trigger_tokens:
            return messages

        split = self._safe_split_index(messages)
        if split <= 0:
            return messages                              # хвост занимает всё — сжимать нечего

        head, tail = messages[:split], messages[split:]
        summary_text = await self._summarize(head)
        summary_msg = HumanMessage(content=f"[Сводка предыдущего диалога]\n{summary_text}")

        # head = индекс в message-записях лога, с которого начинается удержанный хвост.
        # Считаем от message_count (саммари-маркеры в нём не учитываются), НЕ от len(messages):
        # messages[0] после прошлой компакции — саммари, которого в логе как записи нет.
        self._store.append_summary(summary_msg, head=self._store.message_count - len(tail))
        return [summary_msg, *tail]

    def _safe_split_index(self, messages: list[BaseMessage]) -> int:
        """Начало хвоста: последние keep сообщений, но хвост НЕ начинается с ToolMessage.

        Иначе его `AIMessage(tool_calls)` ушёл бы в саммари → осиротевший результат тула →
        ошибка модели. Двигаем границу назад, пока первый элемент хвоста — ToolMessage
        (тогда пара AI↔Tool остаётся целой: обе в хвосте либо обе в саммари).
        """
        split = max(0, len(messages) - self._keep_messages)
        while split > 0 and isinstance(messages[split], ToolMessage):
            split -= 1
        return split
