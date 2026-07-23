"""
Unit-тесты EmbeddingService. Без сети — httpx.MockTransport (как в tests/clients).

Проверяем: построение запроса к OpenAI-совместимому `/embeddings`, разбор ответа
(включая восстановление порядка по index), кэш повторов и проброс таксономии 1.0.
"""

from __future__ import annotations

import httpx
import pytest

from core.clients.errors import UpstreamBadResponse, UpstreamServerError
from core.services.embedding_service import EmbeddingService


def make_service(handler, *, cache_enabled: bool = True) -> EmbeddingService:
    return EmbeddingService(
        "http://test/v1",
        model="embed-model",
        cache_enabled=cache_enabled,
        retries=3,
        backoff_base_s=0.0,
        transport=httpx.MockTransport(handler),
    )


def embeddings_response(vectors: list[list[float]]) -> httpx.Response:
    """OpenAI-формат ответа эмбеддингов."""
    data = [{"embedding": v, "index": i} for i, v in enumerate(vectors)]
    return httpx.Response(200, json={"data": data})


async def test_encode_builds_request_and_parses():
    seen: dict = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["path"] = req.url.path
        seen["method"] = req.method
        seen["body"] = req.content
        return embeddings_response([[0.1, 0.2], [0.3, 0.4]])

    async with make_service(handler) as svc:
        result = await svc.encode(["раз", "два"])

    assert seen["method"] == "POST"
    assert seen["path"] == "/v1/embeddings"
    assert b"embed-model" in seen["body"]
    assert result == [[0.1, 0.2], [0.3, 0.4]]


async def test_encode_one():
    async with make_service(lambda req: embeddings_response([[1.0, 2.0]])) as svc:
        assert await svc.encode_one("текст") == [1.0, 2.0]


async def test_encode_reorders_by_index():
    """Апстрим вернул элементы вперемешку — порядок восстанавливается по index."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"embedding": [2.0], "index": 1},
                    {"embedding": [0.0], "index": 0},
                ]
            },
        )

    async with make_service(handler) as svc:
        assert await svc.encode(["a", "b"]) == [[0.0], [2.0]]


async def test_cache_reuses_and_calls_only_missing():
    """Повторы и дубли не уходят в апстрим; порядок результата сохраняется."""
    calls: list[list[str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        inputs = json.loads(req.content)["input"]
        calls.append(inputs)
        return embeddings_response([[float(len(t))] for t in inputs])

    async with make_service(handler) as svc:
        first = await svc.encode(["aa", "b"])
        # "aa" уже в кэше, "ccc" — новый; дубль "aa" в одном батче не задваивается.
        second = await svc.encode(["aa", "ccc", "aa"])

    assert first == [[2.0], [1.0]]
    assert second == [[2.0], [3.0], [2.0]]
    # Первый вызов — оба текста; второй — только промах "ccc".
    assert calls == [["aa", "b"], ["ccc"]]


async def test_cache_disabled_calls_every_time():
    calls: list[list[str]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        inputs = json.loads(req.content)["input"]
        calls.append(inputs)
        return embeddings_response([[1.0] for _ in inputs])

    async with make_service(handler, cache_enabled=False) as svc:
        await svc.encode(["x"])
        await svc.encode(["x"])

    assert calls == [["x"], ["x"]]


async def test_empty_input_short_circuits():
    async with make_service(lambda req: pytest.fail("должно не ходить в сеть")) as svc:
        assert await svc.encode([]) == []


async def test_missing_data_raises_bad_response():
    async with make_service(lambda req: httpx.Response(200, json={"data": []})) as svc:
        with pytest.raises(UpstreamBadResponse):
            await svc.encode(["x"])


async def test_5xx_propagates_taxonomy():
    async with make_service(lambda req: httpx.Response(503, json={})) as svc:
        with pytest.raises(UpstreamServerError):
            await svc.encode(["x"])
