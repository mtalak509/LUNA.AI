"""
SSE-слой агент-сервера: нормализация StreamEvent в чистый JSON + кадрирование + дренаж.

"""

from __future__ import annotations

import asyncio
import json
import logging

from langchain_core.messages import AIMessageChunk, BaseMessage, message_to_dict

from core.agent.base import StreamEvent
from core.agent.session import AgentSession

_logger = logging.getLogger(__name__)

_SSE_HEARTBEAT_INTERVAL = 30


def _normalize_messages_data(data: object) -> dict:
    """`messages`-кадр: `(AIMessageChunk, metadata)` → компактный JSON-объект для UI.

    Из чанка — текст-дельта, reasoning-дельта (в `content_blocks` есть
    reasoning-блоки с непустым текстом) и tool_call_chunks; из metadata — только
    `langgraph_node` (весь объект UI не нужен и не JSON-чист).
    """
    chunk, metadata = data  # ValueError/TypeError ловит вызывающий → деградация default=str
    if not isinstance(chunk, AIMessageChunk):
        # LangGraph в stream_mode="messages" эмитит и НЕ-дельты модели (например,
        # ToolMessage по завершении тула). Токен-контракт {"text", "reasoning"} — только
        # для чанков модели; прочее — штатной сериализацией (UI берёт результаты тулов
        # из `updates`, но кадр обязан остаться JSON-чистым).
        normalized: dict = {"message": message_to_dict(chunk)}
        if isinstance(metadata, dict) and metadata.get("langgraph_node"):
            normalized["langgraph_node"] = metadata["langgraph_node"]
        return normalized
    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    for block in chunk.content_blocks:
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text") or "")
        elif block_type == "reasoning":
            # Наша форма: текст в content[].text; стандартная — ключ "reasoning".
            if block.get("reasoning"):
                reasoning_parts.append(block["reasoning"])
            for part in block.get("content") or []:
                if isinstance(part, dict):
                    reasoning_parts.append(part.get("text") or "")
    normalized: dict = {
        "text": "".join(text_parts),
        "reasoning": "".join(reasoning_parts) or None,
    }
    if chunk.tool_call_chunks:
        normalized["tool_call_chunks"] = [dict(tc) for tc in chunk.tool_call_chunks]
    if isinstance(metadata, dict) and metadata.get("langgraph_node"):
        normalized["langgraph_node"] = metadata["langgraph_node"]
    return normalized


def _normalize_updates_data(data: object) -> object:
    """`updates`-кадр: LangChain-сообщения → dict'ы, структура `{node: {"messages": […]}}`
    сохраняется. `message_to_dict` — та же штатная сериализация, что у `MessageStore`
    (стабильные поля `type/content/tool_calls/name/status/id` внутри `data`)."""
    if not isinstance(data, dict):
        return data
    normalized: dict = {}
    for node, update in data.items():
        if isinstance(update, dict) and isinstance(update.get("messages"), list):
            update = {
                **update,
                "messages": [
                    message_to_dict(m) if isinstance(m, BaseMessage) else m
                    for m in update["messages"]
                ],
            }
        normalized[node] = update
    return normalized


def _normalize_custom_data(data: object) -> object:
    """`custom`-кадр: вложенные `subagent_event` несут сырые LangChain-объекты в `data`.

    Нормализуем вложенный payload теми же правилами, что top-level `messages`/`updates`,
    иначе `json.dumps(default=str)` превратит их в repr-строки и UI субагента молчит.
    """
    if not isinstance(data, dict):
        return data
    if data.get("type") != "subagent_event":
        return data
    nested_mode = data.get("mode")
    nested_data = data.get("data")
    if nested_mode not in ("messages", "updates"):
        return data
    try:
        normalized_nested = _normalize_event_data(nested_mode, nested_data)
    except Exception:
        _logger.warning(
            "SSE nested normalization failed for subagent_event mode=%s",
            nested_mode,
            exc_info=True,
        )
        return data
    return {**data, "data": normalized_nested}


def _normalize_event_data(mode: str, data: object) -> object:
    """Привести данные кадра к чистому JSON до сериализации.

    `custom`/`control`/`error` уже JSON-чисты — как есть; `messages`/`updates` несут
    объекты LangChain, которые `default=str` превратил бы в repr-строки. Любой сбой
    нормализации → исходные данные; стрим продолжает работу.
    """
    try:
        if mode == "messages":
            return _normalize_messages_data(data)
        if mode == "updates":
            return _normalize_updates_data(data)
        if mode == "custom":
            return _normalize_custom_data(data)
    except Exception:
        _logger.warning("SSE normalization failed for mode=%s, falling back", mode, exc_info=True)
    return data


def _sse_frame(event: StreamEvent) -> str:
    """Один StreamEvent → SSE-кадр. Данные нормализуются в чистый JSON
    (`_normalize_event_data`); `default=str` остаётся последним рубежом."""
    payload = {
        "namespace": event.namespace,
        "mode": event.mode,
        "data": _normalize_event_data(event.mode, event.data),
    }
    body = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event.mode}\ndata: {body}\n\n"


async def _sse_stream(session: AgentSession):
    """Дренаж session.events() в SSE-кадры + heartbeat на долгих паузах (HITL-ожидание).
    """
    gen = session.events()
    next_ev: asyncio.Task | None = None
    try:
        while True:
            if next_ev is None:
                next_ev = asyncio.ensure_future(anext(gen))
            done, _ = await asyncio.wait({next_ev}, timeout=_SSE_HEARTBEAT_INTERVAL)
            if not done:
                yield ": ping\n\n"
                continue
            event = next_ev.result()
            next_ev = None
            yield _sse_frame(event)
    finally:
        if next_ev is not None:
            next_ev.cancel()
