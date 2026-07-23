"""
  Холдер процессных клиентов/сервисов для пула тулов.

  «Розетка» для тулов в обход графа: тулы достают живые клиенты одним
  `get_clients()`, не засоряя per-turn `AgentRuntimeContext`.

  Инициализируется один раз на старте процесса (`set_clients`): прод — `lifespan`
  агентского сервера; дев — `devfront/session.py` / REPL.
  """

from __future__ import annotations

from dataclasses import dataclass

from langchain_core.tools import ToolException
from qdrant_client import AsyncQdrantClient

from core.config import InfraConfig
from core.services.embedding_service import EmbeddingService
from core.services.qdrant_service import QdrantService
from core.services.rag_service import RAGService


@dataclass(frozen=True)
class Clients:
    """Набор процессных зависимостей, видимых тулам через `get_clients()`.

    `rag` = `None`, когда подсистема RAG отключена флагом (`RAG_ENABLED=false`): клиенты не
    строятся, а RAG-тулы вычтены из пула (`assemble_pool`), поэтому `get_clients().rag` в этом
    режиме никто не дёргает. Проводка (импорты сервисов, ветка сборки) сохранена.
    """

    rag: RAGService | None = None

_clients: Clients | None = None


def set_clients(clients: Clients) -> None:
    """Установить холдер. Зовётся один раз на старте процесса (lifespan / дев-вход)."""
    global _clients
    _clients = clients


def get_clients() -> Clients:
    """Получить холдер. Зовётся каждый раз при использовании тул.
    При отсутствии — выбрасывает `ToolException`."""
    if _clients is None:
        raise ToolException(
            "Клиенты не инициализированы: set_clients(...) не был вызван на старте процесса"
        )
    return _clients


def from_config(config: InfraConfig) -> Clients:
    """Собрать `Clients` из `InfraConfig` голыми конструкторами (как в lifespan).

      Узкий helper для дев-входов. Полноценный жизненный цикл клиентов (lazy `httpx`,
      `aclose` в shutdown) проводится в прод-`lifespan` — здесь минимум для обкатки.

      RAG отключён (`RAG_ENABLED=false`) → embedding/qdrant/rag НЕ строим, отдаём пустой холдер.
      """
    if not config.rag_enabled:
        return Clients(rag=None)

    # Embedding-эндпоинт живёт на СВОЁМ сервере с СВОИМ ключом (не GPUSTACK_*).
    embeddings = EmbeddingService(
        base_url=config.embedding.base_url,
        model=config.embedding.model,
        api_key=config.embedding.api_key or None,
        timeout_s=config.http.timeout_s,
        retries=config.http.retries,
        backoff_base_s=config.http.backoff_base_s,
    )

    scheme = "https" if config.qdrant.use_ssl else "http"
    qdrant = QdrantService(
        AsyncQdrantClient(
            url=f"{scheme}://{config.qdrant.host}:{config.qdrant.port}",
            prefix=config.qdrant.prefix or None,
        )
    )

    rag = RAGService(embeddings, qdrant)

    return Clients(rag=rag)