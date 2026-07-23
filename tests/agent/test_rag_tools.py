"""
Unit-тесты: RAG-тулы (rag_tools).

Тулы гоняются через `.ainvoke({...})` — реальный путь вызова. RAGService мокается
AsyncMock'ом и кладётся в холдер (set_clients); сети нет. Проверяем:
- тонкость: тул зовёт rag.search/get_reference с вшитой коллекцией knowledge_base;
- упаковку превью: _pack_hit оставляет id/score + превью-поля и ВЫКИДЫВАЕТ payload;
- пустой результат поиска → явная пометка _EMPTY (не ошибка);
- get_reference None → RagError; UpstreamError → RagError.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import core.agent.tools._clients as holder
from core.agent.tools._clients import Clients
from core.agent.tools.rag_tools import (
    _COLLECTION,
    _EMPTY,
    RagError,
    knowledge_base_get_reference,
    knowledge_base_search,
)
from core.clients.errors import UpstreamServerError


@pytest.fixture()
def rag() -> AsyncMock:
    """AsyncMock RAGService в холдере; сброс холдера после теста."""
    fake = AsyncMock()
    holder.set_clients(Clients(rag=fake))
    yield fake
    holder._clients = None


# --- knowledge_base_search ----------------------------------------------------


async def test_search_thin_and_packs_preview(rag: AsyncMock) -> None:
    rag.search.return_value = [
        {
            "id": "2e94",
            "score": 0.71,
            "description": "Заготовительная операция.",
            "payload": "Операция 005: ...длинный текст...",
            "tags": [],
        },
        {
            "id": "cee0",
            "score": 0.58,
            "description": "Термообработка.",
            "payload": "Операция 015: ...ещё текст...",
            "tags": ["сталь"],
        },
    ]

    out = await knowledge_base_search.ainvoke({"query": "штифт", "top_k": 5})

    # тонкость: вшитая коллекция, ожидаемые позиционные аргументы
    rag.search.assert_awaited_once_with("штифт", _COLLECTION, 5)
    # упаковка: только id/score + превью-поля; payload выброшен
    assert out == [
        {"id": "2e94", "score": 0.71, "description": "Заготовительная операция.", "tags": []},
        {"id": "cee0", "score": 0.58, "description": "Термообработка.", "tags": ["сталь"]},
    ]
    assert all("payload" not in hit for hit in out)


async def test_search_default_top_k(rag: AsyncMock) -> None:
    rag.search.return_value = []
    await knowledge_base_search.ainvoke({"query": "q"})
    rag.search.assert_awaited_once_with("q", _COLLECTION, 5)


async def test_search_pack_omits_missing_preview_fields(rag: AsyncMock) -> None:
    # хит без tags — поле не должно появиться как None
    rag.search.return_value = [{"id": "x", "score": 0.9, "description": "d"}]
    out = await knowledge_base_search.ainvoke({"query": "q"})
    assert out == [{"id": "x", "score": 0.9, "description": "d"}]


async def test_search_empty_returns_marker(rag: AsyncMock) -> None:
    rag.search.return_value = []
    out = await knowledge_base_search.ainvoke({"query": "ничего"})
    assert out == _EMPTY


async def test_search_upstream_error_wrapped(rag: AsyncMock) -> None:
    rag.search.side_effect = UpstreamServerError("502 boom", service="rag")
    with pytest.raises(RagError, match="RAG"):
        await knowledge_base_search.ainvoke({"query": "q"})


# --- knowledge_base_get_reference --------------------------------------------


async def test_get_reference_returns_full_doc(rag: AsyncMock) -> None:
    doc = {
        "id": "2e94",
        "description": "Заготовительная операция.",
        "payload": "Операция 005: полный текст ...",
        "tags": [],
    }
    rag.get_reference.return_value = doc

    out = await knowledge_base_get_reference.ainvoke({"reference_id": "2e94"})

    rag.get_reference.assert_awaited_once_with(_COLLECTION, "2e94")
    assert out == doc  # полный документ, payload на месте


async def test_get_reference_none_raises(rag: AsyncMock) -> None:
    rag.get_reference.return_value = None
    with pytest.raises(RagError, match="не найден"):
        await knowledge_base_get_reference.ainvoke({"reference_id": "missing"})


async def test_get_reference_upstream_error_wrapped(rag: AsyncMock) -> None:
    rag.get_reference.side_effect = UpstreamServerError("503", service="rag")
    with pytest.raises(RagError, match="RAG"):
        await knowledge_base_get_reference.ainvoke({"reference_id": "x"})
