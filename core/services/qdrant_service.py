"""
QdrantService — тонкая async-обёртка над `qdrant-client` (AsyncQdrantClient).


Транспорт Qdrant не делит BaseHTTPClient (свой SDK); общими с прочими клиентами
остаются только InfraConfig и таксономия ошибок (применяет вызывающий слой).
"""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qmodels
from qdrant_client.conversions.common_types import Filter, Record, ScoredPoint

logger = logging.getLogger(__name__)


class QdrantService:
    """Поиск/выборка по существующим коллекциям Qdrant.

    Создаётся с готовым `AsyncQdrantClient`:

        from qdrant_client import AsyncQdrantClient
        QdrantService(AsyncQdrantClient(url="http://qdrant:6333"))
    """

    def __init__(self, client: AsyncQdrantClient) -> None:
        self._client = client

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 5,
        *,
        filters: Filter | None = None,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Векторный поиск. Возвращает `[{"id", "score", **payload}]` (payload as-is —
        коллекции имеют произвольную схему, напр. `knowledge_base`)."""
        logger.debug(
            "[qdrant] -> search collection=%s top_k=%s dim=%d filter=%s threshold=%s",
            collection, top_k, len(query_vector), filters is not None, score_threshold,
        )
        response = await self._client.query_points(
            collection_name=collection,
            query=query_vector,
            limit=top_k,
            query_filter=filters,
            score_threshold=score_threshold,
            with_payload=True,
        )
        logger.debug(
            "[qdrant] <- search collection=%s hits=%d", collection, len(response.points)
        )
        return [self._scored_to_dict(point) for point in response.points]

    async def get_by_id(self, collection: str, point_id: str | int) -> dict[str, Any] | None:
        """Полная точка по id: `{"id", **payload}` или None, если не найдена."""
        logger.debug("[qdrant] -> get_by_id collection=%s id=%s", collection, point_id)
        records = await self._client.retrieve(
            collection_name=collection,
            ids=[point_id],
            with_payload=True,
            with_vectors=False,
        )
        logger.debug(
            "[qdrant] <- get_by_id collection=%s id=%s found=%s",
            collection, point_id, bool(records),
        )
        if not records:
            return None
        return self._record_to_dict(records[0])

    async def find_by_field(
        self, collection: str, field: str, value: Any, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Точный payload-lookup БЕЗ вектора: точки, где `payload[field] == value`.

        Через `scroll` с `query_filter` (вектор не нужен) — переиспользуем паттерн
        `get_all_points`. Возвращает `[{"id", **payload}]` (как `get_by_id`, но списком).
        Для точного lookup'а по значению поля вместо семантического поиска."""
        logger.debug(
            "[qdrant] -> find_by_field collection=%s field=%s value=%s limit=%d",
            collection, field, value, limit,
        )
        scroll_filter = qmodels.Filter(
            must=[qmodels.FieldCondition(key=field, match=qmodels.MatchValue(value=value))]
        )
        records, _ = await self._client.scroll(
            collection_name=collection,
            scroll_filter=scroll_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        logger.debug(
            "[qdrant] <- find_by_field collection=%s hits=%d", collection, len(records)
        )
        return [self._record_to_dict(r) for r in records]

    async def aclose(self) -> None:
        await self._client.close()

    async def get_all_points(
        self, collection: str, *, batch: int = 256
    ) -> list[dict[str, Any]]:
        """Выгрузить все точки коллекции постранично.

        `scroll` отдаёт кортеж `(records, next_page_offset)`; крутим, пока offset не None.
        """
        logger.debug("[qdrant] -> get_all_points collection=%s batch=%d", collection, batch)
        results: list[dict[str, Any]] = []
        offset = None
        while True:
            records, offset = await self._client.scroll(
                collection_name=collection,
                limit=batch,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            results.extend(self._record_to_dict(r) for r in records)
            if offset is None:
                break
        logger.debug(
            "[qdrant] <- get_all_points collection=%s total=%d", collection, len(results)
        )
        return results

    # --- helpers ------------------------------------------------------------

    @staticmethod
    def _scored_to_dict(point: ScoredPoint) -> dict[str, Any]:
        return {"id": str(point.id), "score": point.score, **(point.payload or {})}

    @staticmethod
    def _record_to_dict(record: Record) -> dict[str, Any]:
        return {"id": str(record.id), **(record.payload or {})}


# if __name__ == "__main__":
#     import asyncio
#     from core.config import get_config
#     import logging
#     logging.basicConfig(level=logging.DEBUG)

#     cfg = get_config()
#     async def main() -> None:
#         scheme = "https" if cfg.qdrant.use_ssl else "http"
#         url = f"{scheme}://{cfg.qdrant.host}:{cfg.qdrant.port}"
#         qdrant_service = QdrantService(
#             client=AsyncQdrantClient(url=url, prefix=cfg.qdrant.prefix or None)
#         )

#         points = await qdrant_service.get_all_points(collection="knowledge_base")
#         print(f"knowledge_base: {len(points)} точек")
#         if points:
#             print("первая:", points[0])
#         await qdrant_service.aclose()

#     asyncio.run(main())