# syntax=docker/dockerfile:1
#
# Образ агент-сервера LUNA: пакет `core/` с точкой входа `core.app.server:create_app`.
#
# Конфиг в образ НЕ запекается: env перекрывает .env-файл в pydantic-settings,
# локально — env_file в compose. Секретам в слоях образа не место.
#
# Контекст сборки — корень репозитория (CI: docker build -f docker/Dockerfile .).

# ──────────────────────────────────────────────────────────────────────────────
# Стадия build: зависимости + пакет core в изолированный venv.
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# build-essential нужен на случай sdist-зависимостей без готовых wheel'ов.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv "$VIRTUAL_ENV"

WORKDIR /app

# Метаданные пакета + сам код. pip install . тянет зависимости из pyproject (диапазоны).
COPY pyproject.toml README.md ./
COPY core/ ./core/
RUN pip install .

# ──────────────────────────────────────────────────────────────────────────────
# Стадия runtime: тонкий образ + готовый venv.
# ──────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# bash/jq/git — универсальные shell-инструменты агента (v2-sandbox-model.md §1);
# libgomp1 — рантайм onnxruntime (fastembed): без него агент-сервер падает на старте
# при сборке EmbeddingService в lifespan.
RUN apt-get update \
    && apt-get install -y --no-install-recommends bash jq git libgomp1 \
    && rm -rf /var/lib/apt/lists/*

ENV VIRTUAL_ENV=/opt/venv \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY core/ ./core/
COPY scripts/ ./scripts/

# Процедурная память в каноническом RO-расположении /procedures/ (v2-storage.md §2.1).
# NB: рантайм-код (bootstrap) сейчас читает core/procedures/ из пакета — /procedures/
# держим как канон для sandbox-модели; источник один, копия дешёвая.
COPY core/procedures/ /procedures/

# Статика дев-фронта (plans/dev-frontend-implementation-plan.md): отдаётся тем же
# FastAPI-процессом (StaticFiles), отдельного фронт-сервиса нет.
COPY devfront/static/ ./devfront/static/

# Agent working zones are ephemeral (container dies → session dies). The session root
# ~/.luna/sessions is created at runtime under appuser's home — nothing to pre-create.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app

USER appuser

# 8000 — агент-сервер.
EXPOSE 8000

# NB: порт хардкодим тут (CMD не читает env).
CMD ["uvicorn", "core.app.server:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
