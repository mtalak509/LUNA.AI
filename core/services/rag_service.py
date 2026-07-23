"""
RAGService — тонкий слой над EmbeddingService + QdrantService.

Склеивает два шага семантического поиска: векторизация запроса (EmbeddingService) и
поиск по коллекции Qdrant (QdrantService). Сам ни к сети, ни к SDK не ходит — только
оркеструет два сервиса; таксономию ошибок 1.0 пробрасывают они.

Используется тулами `rag_search` и `rag_get_reference`.

Слой нейтрален к LangChain/LangGraph.
"""

from __future__ import annotations

import logging
from typing import Any

from qdrant_client.conversions.common_types import Filter

from core.services.embedding_service import EmbeddingService
from core.services.qdrant_service import QdrantService

logger = logging.getLogger(__name__)


class RAGService:
    """Семантический поиск по коллекциям Qdrant.

    Создаётся с готовыми сервисами (оба живут весь lifespan процесса):

        RAGService(embedding_service, qdrant_service)

    Результаты Qdrant пробрасываются как есть (`{"id", "score", **payload}`) — раскладку
    payload на текст/метаданные делает вызывающий тул под свой формат цитат.
    """

    def __init__(
        self,
        embeddings: EmbeddingService,
        qdrant: QdrantService,
        *,
        default_top_k: int = 5,
    ) -> None:
        self._embeddings = embeddings
        self._qdrant = qdrant
        self._default_top_k = default_top_k

    async def search(
        self,
        query: str,
        collection: str,
        top_k: int | None = None,
        *,
        filters: Filter | None = None,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Векторизовать запрос и найти ближайшие чанки.

        Возвращает результаты Qdrant как есть — `[{"id", "score", **payload}]`,
        отсортированные по убыванию score.
        """
        limit = top_k if top_k is not None else self._default_top_k
        logger.debug(
            "[rag] -> search collection=%s top_k=%s threshold=%s",
            collection, limit, score_threshold,
        )
        vector = await self._embeddings.encode_one(query)
        hits = await self._qdrant.search(
            collection,
            vector,
            limit,
            filters=filters,
            score_threshold=score_threshold,
        )
        logger.debug("[rag] <- search collection=%s hits=%d", collection, len(hits))
        return hits

    async def get_reference(
        self, collection: str, doc_id: str | int
    ) -> dict[str, Any] | None:
        """Полный документ по id: `{"id", **payload}` или None, если не найден."""
        return await self._qdrant.get_by_id(collection, doc_id)

    async def find_by_field(
        self, collection: str, field: str, value: Any, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        """Точный payload-lookup без вектора (passthrough в `QdrantService`).

        Для тулов, которым нужен точный узел по значению поля, а не семантика.
        Тул ходит через холдер (`get_clients().rag`), не зная про Qdrant напрямую.
        """
        return await self._qdrant.find_by_field(collection, field, value, limit=limit)

# if __name__ == "__main__":
#     import asyncio
#     from core.config import get_config
#     from core.services.embedding_service import EmbeddingService
#     from core.services.qdrant_service import QdrantService
#     from qdrant_client import AsyncQdrantClient

#     cfg = get_config()
#     embedding_service = EmbeddingService(
#         base_url=cfg.gpustack.base_url,
#         model=cfg.gpustack.embedding_model,
#         api_key=cfg.gpustack.api_key or None
#     )
#     qdrant_service = QdrantService(
#         client=AsyncQdrantClient(url=f"http://{cfg.qdrant.host}:{cfg.qdrant.port}", prefix=cfg.qdrant.prefix or None)
#     )
#     rag_service = RAGService(embedding_service, qdrant_service)

#     async def main() -> None:
#         result = await rag_service.search("Hello, world!", "knowledge_base")
#         print(result)
#         print("-" * 100)
#         reference = await rag_service.get_reference("knowledge_base", "916967ef-27e4-45f8-9fbd-8c4b372e0806")
#         print(reference)

#     asyncio.run(main())