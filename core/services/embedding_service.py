"""
EmbeddingService — текст → вектор через внешний OpenAI-совместимый `/embeddings`
(vLLM/GPUStack; параметры берутся из `embedding.*` конфига: base_url, model, api_key —
отдельная группа от `gpustack.*`: embedding-модель живёт на другом сервере с другим ключом).

Тонкий клиент поверх BaseHTTPClient: батч-векторизация + кэш векторов для повторных
запросов. Локального backend (fastembed) нет — модель держит общий сервис, не контейнер.

Слой нейтрален к LangChain/LangGraph.
"""

from __future__ import annotations

from typing import Any

from core.clients.base import BaseHTTPClient
from core.clients.errors import UpstreamBadResponse


class EmbeddingService(BaseHTTPClient):
    """Async-векторизатор. Создаётся голым конструктором в lifespan процесса:

        EmbeddingService(cfg.embedding.base_url, model=cfg.embedding.model,
                         api_key=cfg.embedding.api_key or None)
    """

    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        service: str = "embedding",
        cache_enabled: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(base_url, service=service, **kwargs)
        self.model = model
        # Кэш text → вектор. None — кэш выключен.
        self._cache: dict[str, list[float]] | None = {} if cache_enabled else None

    async def encode(self, texts: list[str]) -> list[list[float]]:
        """Векторизовать батч. Повторы берутся из кэша, в апстрим уходят только промахи.

        Порядок результата соответствует порядку `texts` (включая дубли).
        """
        texts = list(texts)
        if not texts:
            return []
        if self._cache is None:
            return await self._embed(texts)

        # Уникальные промахи (dict.fromkeys сохраняет порядок и убирает дубли).
        missing = [t for t in dict.fromkeys(texts) if t not in self._cache]
        if missing:
            vectors = await self._embed(missing)
            self._cache.update(zip(missing, vectors, strict=True))
        return [self._cache[t] for t in texts]

    async def encode_one(self, text: str) -> list[float]:
        """Векторизовать одну строку."""
        return (await self.encode([text]))[0]

    # --- internals ----------------------------------------------------------

    async def _embed(self, texts: list[str]) -> list[list[float]]:
        """Один вызов `/embeddings`; ответ OpenAI: {"data": [{"embedding", "index"}]}."""
        url = f"{self.base_url}/embeddings"
        payload = {"model": self.model, "input": texts}
        data = await self._post(url, json=payload)

        items = data.get("data") if isinstance(data, dict) else None
        if not items:
            raise UpstreamBadResponse(
                "no embeddings in response", service=self.service
            )
        # Восстановить порядок по index (апстрим может вернуть не по порядку).
        ordered = sorted(items, key=lambda d: d.get("index", 0))
        return [item["embedding"] for item in ordered]


if __name__ == "__main__":
    import asyncio
    from core.config import get_config

    cfg = get_config()

    async def main() -> None:
        embedding_service = EmbeddingService(
            base_url=cfg.embedding.base_url,
            model=cfg.embedding.model,
            api_key=cfg.embedding.api_key or None
        )

        texts = ["Hello, world!", "Hello, world!"]
        vectors = await embedding_service.encode(texts)
        print(vectors)

    asyncio.run(main())