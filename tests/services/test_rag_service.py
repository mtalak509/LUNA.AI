"""
Unit-тесты RAGService. Без сети и SDK — EmbeddingService и QdrantService замоканы.

Проверяем: склейку шагов (encode запроса → search по его вектору), проброс
top_k/filters/threshold в Qdrant, проброс результатов Qdrant как есть,
дефолтный top_k и проброс get_reference.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from core.services.rag_service import RAGService


def make_service(*, default_top_k: int = 5):
    embeddings = AsyncMock()
    qdrant = AsyncMock()
    svc = RAGService(embeddings, qdrant, default_top_k=default_top_k)
    return svc, embeddings, qdrant


async def test_search_encodes_query_then_searches():
    svc, embeddings, qdrant = make_service()
    embeddings.encode_one.return_value = [0.1, 0.2, 0.3]
    qdrant.search.return_value = []

    await svc.search("найти штангенциркуль", "refs", top_k=3)

    embeddings.encode_one.assert_awaited_once_with("найти штангенциркуль")
    qdrant.search.assert_awaited_once()
    args = qdrant.search.await_args
    assert args.args == ("refs", [0.1, 0.2, 0.3], 3)
    assert args.kwargs["filters"] is None
    assert args.kwargs["score_threshold"] is None


async def test_search_returns_qdrant_hits_as_is():
    svc, embeddings, qdrant = make_service()
    embeddings.encode_one.return_value = [0.0]
    hits = [
        {"id": "1", "score": 0.9, "text": "чанк-А", "doc_id": "g123"},
        {"id": "2", "score": 0.7, "text": "чанк-Б", "doc_id": "g124"},
    ]
    qdrant.search.return_value = hits

    result = await svc.search("q", "refs")

    assert result == hits


async def test_search_uses_default_top_k():
    svc, embeddings, qdrant = make_service(default_top_k=7)
    embeddings.encode_one.return_value = [0.0]
    qdrant.search.return_value = []

    await svc.search("q", "refs")

    assert qdrant.search.await_args.args[2] == 7


async def test_search_passes_filter_and_threshold():
    svc, embeddings, qdrant = make_service()
    embeddings.encode_one.return_value = [0.0]
    qdrant.search.return_value = []
    sentinel = object()

    await svc.search("q", "refs", filters=sentinel, score_threshold=0.4)

    kwargs = qdrant.search.await_args.kwargs
    assert kwargs["filters"] is sentinel
    assert kwargs["score_threshold"] == 0.4


async def test_get_reference_delegates_to_qdrant():
    svc, embeddings, qdrant = make_service()
    qdrant.get_by_id.return_value = {"id": "p1", "text": "полный документ"}

    result = await svc.get_reference("refs", "p1")

    qdrant.get_by_id.assert_awaited_once_with("refs", "p1")
    assert result == {"id": "p1", "text": "полный документ"}
    embeddings.encode_one.assert_not_awaited()


async def test_get_reference_returns_none_when_missing():
    svc, embeddings, qdrant = make_service()
    qdrant.get_by_id.return_value = None

    assert await svc.get_reference("refs", "nope") is None
