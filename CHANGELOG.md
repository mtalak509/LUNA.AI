# Changelog

Все заметные изменения LUNA документируются здесь.

Формат основан на [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/),
проект следует [Semantic Versioning](https://semver.org/lang/ru/).

Секция `[Unreleased]` пополняется по ходу работы; при релизе она переименовывается
в `[X.Y.Z] — YYYY-MM-DD`, а сверху заводится новый пустой `[Unreleased]`.

## [Unreleased]

### Added
- Выбор LLM-провайдера флагом `LLM_PROVIDER` (`gpustack` | `openrouter` | `ollama`)
  в `build_chat_model`; добавлена группа настроек OpenRouter.
- Версия пакета в баннере (`scripts/banner.py`) через `importlib.metadata` — единый
  источник правды с `pyproject.toml`.

### Changed
- Оживлена проводка Ollama как LLM-провайдера (ранее конфиг присутствовал, но в рантайме
  не использовался); работает через OpenAI-совместимый эндпоинт (`OLLAMA_BASE_URL` + `/v1`).

### Removed
- MLflow: серверный образ `docker/mlflow/` (сервисный код и зависимости удалены ранее).
- Устаревшее упоминание `alembic/` в комментарии `.dockerignore` (слой БД/Alembic удалён).
