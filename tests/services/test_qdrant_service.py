"""
Unit-тесты QdrantService. Без живого Qdrant — мок на уровне AsyncQdrantClient.

Проверяем: построение запроса (collection/limit/filter/threshold), маппинг
результата (search → id+score+payload, get_by_id → id+payload), None при отсутствии.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.services.qdrant_service import QdrantService


def make_service() -> tuple[QdrantService, AsyncMock]:
    client = AsyncMock()
    return QdrantService(client), client


async def test_search_builds_query_and_maps_results():
    svc, client = make_service()
    client.query_points.return_value = SimpleNamespace(
        points=[
            SimpleNamespace(id=1, score=0.9, payload={"name": "класс-А"}),
            SimpleNamespace(id="uuid-2", score=0.7, payload={"name": "класс-Б"}),
        ]
    )

    result = await svc.search("knowledge_base", [0.1, 0.2], top_k=3, score_threshold=0.5)

    client.query_points.assert_awaited_once()
    kwargs = client.query_points.await_args.kwargs
    assert kwargs["collection_name"] == "knowledge_base"
    assert kwargs["query"] == [0.1, 0.2]
    assert kwargs["limit"] == 3
    assert kwargs["score_threshold"] == 0.5
    assert kwargs["query_filter"] is None
    assert result == [
        {"id": "1", "score": 0.9, "name": "класс-А"},
        {"id": "uuid-2", "score": 0.7, "name": "класс-Б"},
    ]


async def test_search_passes_filter():
    svc, client = make_service()
    client.query_points.return_value = SimpleNamespace(points=[])
    sentinel = object()

    await svc.search("c", [0.0], filters=sentinel)

    assert client.query_points.await_args.kwargs["query_filter"] is sentinel


async def test_search_handles_empty_payload():
    svc, client = make_service()
    client.query_points.return_value = SimpleNamespace(
        points=[SimpleNamespace(id=5, score=0.1, payload=None)]
    )

    result = await svc.search("c", [0.0])

    assert result == [{"id": "5", "score": 0.1}]


async def test_get_by_id_returns_record():
    svc, client = make_service()
    client.retrieve.return_value = [SimpleNamespace(id="p1", payload={"text": "док"})]

    result = await svc.get_by_id("refs", "p1")

    kwargs = client.retrieve.await_args.kwargs
    assert kwargs["collection_name"] == "refs"
    assert kwargs["ids"] == ["p1"]
    assert kwargs["with_vectors"] is False
    assert result == {"id": "p1", "text": "док"}


async def test_get_by_id_returns_none_when_missing():
    svc, client = make_service()
    client.retrieve.return_value = []

    assert await svc.get_by_id("refs", "nope") is None


async def test_find_by_field_builds_filter_and_maps():
    svc, client = make_service()
    client.scroll.return_value = (
        [
            SimpleNamespace(id="c1", payload={"className": "Концевые фрезы"}),
            SimpleNamespace(id=7, payload={"className": "Концевые фрезы", "count": 12}),
        ],
        None,  # next_page_offset
    )

    result = await svc.find_by_field("knowledge_base", "className", "Концевые фрезы", limit=5)

    client.scroll.assert_awaited_once()
    kwargs = client.scroll.await_args.kwargs
    assert kwargs["collection_name"] == "knowledge_base"
    assert kwargs["limit"] == 5
    assert kwargs["with_vectors"] is False
    # фильтр построен по полю/значению (без вектора)
    condition = kwargs["scroll_filter"].must[0]
    assert condition.key == "className"
    assert condition.match.value == "Концевые фрезы"
    # маппинг: id в строку + payload as-is (без score — это не векторный поиск)
    assert result == [
        {"id": "c1", "className": "Концевые фрезы"},
        {"id": "7", "className": "Концевые фрезы", "count": 12},
    ]


async def test_find_by_field_empty():
    svc, client = make_service()
    client.scroll.return_value = ([], None)
    assert await svc.find_by_field("knowledge_base", "className", "нет такого") == []


async def test_aclose_closes_client():
    svc, client = make_service()
    await svc.aclose()
    client.close.assert_awaited_once()
