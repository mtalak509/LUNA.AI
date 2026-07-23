"""
RAG-тулы — плоский пул @tool поверх RAGService.

Коллекция ВШИТА в тул (knowledge_base), а не передаётся аргументом.

Двухшаговый паттерн (полный текст справочника крупный, нельзя лить в контекст целиком):
knowledge_base_search            → превью хитов (id/score + description + tags) для выбора;
knowledge_base_get_reference(id) → полный документ (с текстом payload) по явному выбору агента.

Ошибки: RAGService пробрасывает UpstreamError — ловим и оборачиваем в RagError;
ToolErrorMiddleware превратит в ToolMessage(status="error") → self-correction.
Пустой результат поиска — НЕ ошибка: явная пометка «ничего не найдено».
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, TypeVar

from langchain_core.tools import ToolException
from core.agent.tools._clients import get_clients
from core.clients.errors import UpstreamError
from core.agent.tools import agent_tool


_COLLECTION = "knowledge_base"
_EMPTY = "Ничего не найдено."
_PREVIEW_FIELDS = ("description", "tags")
_T = TypeVar("_T")


class RagError(ToolException):
    """Ошибка RAG-тула — отдаётся модели для self-correction.

    Обертка над UpstreamError клиента: ToolErrorMiddleware превратит её в
    ToolMessage(status="error") → self-correction.
    """

async def _awrap(coro: Awaitable[_T]) -> _T:
    """Обернуть вызов RAGService в RagError, если он пробрасывает UpstreamError."""
    try:
        return await coro
    except UpstreamError as e:
        raise RagError(f"Ошибка обращения к RAG: {e}") from e

def _pack_hit(hit: dict[str, Any]) -> dict[str, Any]:
    """Qdrant-hit → компактное превью БЕЗ полного текста payload (только превью-поля).

    id/score сохраняем целиком; из payload берём лишь _PREVIEW_FIELDS (description/tags).
    Полный текст агент тянет адресно через knowledge_base_get_reference.
    """
    packed: dict[str, Any] = {"id": hit.get("id"), "score": hit.get("score")}
    for field in _PREVIEW_FIELDS:
        if field in hit:
            packed[field] = hit[field]
    return packed


@agent_tool(is_write=False, main_only=False, fs_scope=None, feature="rag")
async def knowledge_base_search(query: str, top_k: int = 5) -> Any:
    """Семантический поиск по базе знаний RAG (справочники НСИ, материалы и т.п.)

    Возвращает ранжированные по сходству превью: id, score, description, tags.
    Полный текст справочника — отдельным вызовом knowledge_base_get_reference(id).

    Args:
        query: Поисковый запрос на естественном языке
        top_k: Максимальное количество результатов
    """
    hits = await _awrap(get_clients().rag.search(query, _COLLECTION, top_k))
    if not hits:
        return _EMPTY
    return [_pack_hit(hit) for hit in hits]


@agent_tool(is_write=False, main_only=False, fs_scope=None, feature="rag")
async def knowledge_base_get_reference(reference_id: str) -> dict[str, Any]:
    """Получить полный текст справочника по id из результата knowledge_base_search.

    Args:
        reference_id: id точки, полученный из knowledge_base_search.
    """
    data = await _awrap(get_clients().rag.get_reference(_COLLECTION, reference_id))
    if data is None:
        raise RagError(f"Справочник не найден: id={reference_id}")
    return data