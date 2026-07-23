"""
Инфраструктурный конфиг: читается из переменных окружения / `.env` один раз на старте
процесса и дальше read-only.
"""

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BeforeValidator, Field
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Путь к .env вычисляем от расположения config.py (корень репо = на уровень выше core/),
# а не относительно CWD — иначе запуск из другой папки молча уводит конфиг на дефолты.
# Файл лежит в корне репо (.env).
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"

# URL без хвостового слэша — чтобы `base_url + "/path"` не давал "//path".
BaseUrl = Annotated[str, AfterValidator(lambda v: v.rstrip("/"))]


def _split_csv(v: object) -> object:
    """Принять список как CSV-строку из env (`*` или `a,b,c`), не только как JSON."""
    if isinstance(v, str):
        return [item.strip() for item in v.split(",") if item.strip()]
    return v


# list[str], который читается из env как CSV (`*` или `a,b,c`), а не JSON.
# NoDecode отключает JSON-декод на уровне источника — тогда сырую строку забирает _split_csv.
CsvList = Annotated[list[str], NoDecode, BeforeValidator(_split_csv)]

# Общие настройки чтения окружения для всех групп.
_ENV = SettingsConfigDict(
    env_file=_ENV_FILE,
    env_file_encoding="utf-8",
    extra="ignore",  # лишние переменные окружения не валят старт
    case_sensitive=False,
)


# LUNA's home directory — the single fixed on-disk root, shared by both interfaces
# (WEB, CLI) and intentionally NOT env-overridable. Like Claude Code's `~/.claude`,
# `Path.home()` resolves it per-OS (Windows → %USERPROFILE%, *nix → $HOME), keeping OS
# differences in stdlib with no platform branching.
LUNA_HOME = Path.home() / ".luna"

# WEB session root — a dedicated `sessions/` subdir, never `~/.luna/` itself: start-up
# cleanup (`_wipe_stale_sessions` drops every session_root subdirectory) must not touch
# the CLI's state or future config/logs sharing the home.
_SESSION_ROOT = LUNA_HOME / "sessions"


class ServerSettings(BaseSettings):
    """HTTP-сервер (MainAgent внутри контейнера / оркестратор)."""

    model_config = _ENV  # без префикса: HOST / PORT / RELOAD / ALLOWED_ORIGINS

    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False
    allowed_origins: CsvList = Field(default_factory=lambda: ["*"])
    root_path: str = ""

    @property
    def session_root(self) -> str:
        """Fixed session root (`~/.luna/sessions`); by design not read from env."""
        return str(_SESSION_ROOT)


class LoggingSettings(BaseSettings):
    model_config = SettingsConfigDict(**_ENV, env_prefix="LOG_")

    level: str = "INFO"  # ← LOG_LEVEL
    to_file: bool = False  # ← LOG_TO_FILE
    file: str = "app.log"  # ← LOG_FILE


class GPUStackSettings(BaseSettings):
    """LLM через GPUStack / vLLM по OpenAI-совместимому протоколу (ChatOpenAI)."""

    model_config = SettingsConfigDict(**_ENV, env_prefix="GPUSTACK_")

    base_url: BaseUrl = "http://localhost:8080/v1"
    api_key: str = ""
    llm_model: str = "qwen3.6-35b-a3b-awq-4bit"

    timeout: int = 120
    retries: int = 2


class OpenRouterSettings(BaseSettings):
    """LLM через OpenRouter (OpenAI-совместимый API, Chat Completions).

    Публичный шлюз к множеству моделей под одним ключом. В отличие от GPUStack
    работает по Chat Completions, а не Responses API (см. `build_chat_model`).
    """

    model_config = SettingsConfigDict(**_ENV, env_prefix="OPENROUTER_")

    base_url: BaseUrl = "https://openrouter.ai/api/v1"
    api_key: str = ""
    llm_model: str = "qwen/qwen3-235b-a22b"

    timeout: int = 120
    retries: int = 2


class EmbeddingSettings(BaseSettings):
    """Embedding-эндпоинт (OpenAI-совместимый `/embeddings`).

    Отдельная группа от `GPUSTACK_*`: LLM и embedding-модель живут на РАЗНЫХ
    серверах с разными ключами — свой base_url и api_key обязательны.
    """

    model_config = SettingsConfigDict(**_ENV, env_prefix="EMBEDDING_")

    base_url: BaseUrl = "http://localhost:8080/v1"  # ← EMBEDDING_BASE_URL
    api_key: str = ""  # ← EMBEDDING_API_KEY
    model: str = "nomic-embed-text-v2-moe:latest"  # ← EMBEDDING_MODEL


class OllamaSettings(BaseSettings):
    """LLM через Ollama. Выступает в качестве fallback для GPUStack."""

    model_config = SettingsConfigDict(**_ENV, env_prefix="OLLAMA_")

    base_url: BaseUrl = "http://localhost:11434"
    api_key: str = ""
    llm_model: str = "qwen3.6-35b-a3b-awq-4bit"
    embedding_model: str = "nomic-embed-text-v2-moe:latest"
    timeout: int = 60
    retries: int = 2
    embed_keep_alive: str = "-1m"


class QdrantSettings(BaseSettings):
    model_config = SettingsConfigDict(**_ENV, env_prefix="QDRANT_")

    host: str = "qdrant"  # ← QDRANT_HOST
    port: int = 6333  # ← QDRANT_PORT
    use_ssl: bool = False  # ← QDRANT_USE_SSL
    prefix: str = ""  # ← QDRANT_PREFIX
    # vector_dimension: int = Field(default=384, validation_alias="VECTOR_DIMENSION")


class HTTPSettings(BaseSettings):
    """Дефолты транспорта для BaseHTTPClient (embedding-endpoint и внешние HTTP-клиенты)."""

    model_config = SettingsConfigDict(**_ENV, env_prefix="HTTP_")

    timeout_s: float = 30.0  # ← HTTP_TIMEOUT_S
    retries: int = 3  # ← HTTP_RETRIES
    backoff_base_s: float = 0.5  # ← HTTP_BACKOFF_BASE_S


class InfraConfig(BaseSettings):
    """Корневой инфраструктурный конфиг. Композиция доменных групп.

    Использование:
        from core.config import get_config
        cfg = get_config()
        cfg.qdrant.host, cfg.gpustack.base_url, cfg.http.timeout_s
    """

    model_config = _ENV

    server: ServerSettings = Field(default_factory=ServerSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    gpustack: GPUStackSettings = Field(default_factory=GPUStackSettings)
    openrouter: OpenRouterSettings = Field(default_factory=OpenRouterSettings)
    embedding: EmbeddingSettings = Field(default_factory=EmbeddingSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    qdrant: QdrantSettings = Field(default_factory=QdrantSettings)
    http: HTTPSettings = Field(default_factory=HTTPSettings)

    # Выбор LLM-провайдера для сборки чат-модели (build_chat_model). Только LLM —
    # embedding-эндпоинт (EMBEDDING_*) не зависит от этого флага.
    llm_provider: Literal["gpustack", "openrouter", "ollama"] = Field(
        default="openrouter", validation_alias="LLM_PROVIDER"
    )

    # Feature-флаги отключаемых подсистем (проводка сохранена, активация гейтится флагом).
    rag_enabled: bool = Field(default=False, validation_alias="RAG_ENABLED")

    @property
    def disabled_features(self) -> set[str]:
        """Подсистемы, вычитаемые из пула тулов при сборке (см. `assemble_pool`)."""
        disabled: set[str] = set()
        if not self.rag_enabled:
            disabled.add("rag")
        return disabled


@lru_cache
def get_config() -> InfraConfig:
    """Единый кэшированный инстанс — env читается один раз на процесс."""
    return InfraConfig()
