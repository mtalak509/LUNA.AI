"""
Плоский пул тулов и его сборка под конкретного агента.

Принцип V2 (П2): **один общий пул `@tool` на всех агентов**; специализация субагента —
декларативна (`agent.md` + скиллы), а не отдельный класс/реестр тулов. Поэтому здесь нет
per-agent whitelist'ов: есть один пул, на каждом туле — декларативные атрибуты, а нужный
срез агенту собирается **вычитанием** (`assemble_pool`), а не набором с нуля.

Содержит:
- `agent_tool` — тонкая обёртка над `@tool`, кладущая атрибуты фильтрации в `tool.metadata`;
- ридеры атрибутов (`is_write_tool`/`is_main_only`/`fs_scope_of`) — единая точка чтения для
  `assemble_pool` и `PermissionMiddleware`;
- `assemble_pool` — статическая сборка пула под `agent_scope` (+ опционально режим Plan).

Сами тулы (файловые, `tp_*`, RAG, HITL, Task) живут в своих модулях — здесь только
механизм атрибутов и сборки.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import BaseTool, tool

# --- ключи атрибутов в tool.metadata ------------------------------------------------
# Вынесены в константы, чтобы писатель (agent_tool) и читатели (ридеры/middleware) не
# расходились в строковых ключах.
META_IS_WRITE = "is_write"
META_MAIN_ONLY = "main_only"
META_FS_SCOPE = "fs_scope"
META_FEATURE = "feature"

# Зоны ФС, в которых агент вправе оперировать файловыми тулами.
#   None у тула  → тул не привязан к ФС (RAG/HTTP/...) → доступен всем.
#   строка       → тул работает в конкретной зоне; виден, только если зона в наборе скоупа.
# ВАЖНО: имена зон и точная политика — часть скоупа файловых тулов. Здесь —
# минимальный каркас-шов под архитектуру V2 (workspace — RW только у MainAgent, у субагента
# своя зона; procedures — RO у обоих). Значения могут уточниться при реализации скоупа.
_FS_ZONES_BY_SCOPE: dict[str, set[str]] = {
    "main": {"workspace", "procedures"},
    "subagent": {"subagent", "procedures"},
}


def agent_tool(
    name_or_callable: str | Callable | None = None,
    *,
    is_write: bool = False,
    main_only: bool = False,
    fs_scope: str | None = None,
    feature: str | None = None,
    **tool_kwargs: Any,
) -> BaseTool | Callable[[Callable], BaseTool]:
    """`@tool`-обёртка, навешивающая на тул декларативные атрибуты фильтрации.

    Атрибуты:
    - `is_write`  — тул совершает запись/мутацию. В Plan-режиме `PermissionMiddleware` (2.3)
      вычитает такие тулы на границе вызова модели — в Plan модель их физически не видит.
    - `main_only` — тул доступен только MainAgent; у любого субагента вычитается (shorthand
      для exclude, НЕ замкнутый per-type whitelist — см. П2).
    - `fs_scope`  — зона ФС, в которой тул оперирует (`None` = не привязан к ФС).
    - `feature`   — отключаемая подсистема, к которой относится тул (`None` = ядро, всегда в
      пуле). Если имя фичи в `disabled_features` при сборке — тул вычитается (`assemble_pool`).

    Поддерживает те же формы, что и `@tool`:
        @agent_tool
        @agent_tool(is_write=True, fs_scope="workspace")
        @agent_tool("custom_name", main_only=True, description="...")

    Реализация: строим тул штатным `tool(...)`, затем кладём атрибуты в `.metadata`. Сам
    `@tool` аргумент `metadata` не принимает (есть только provider-specific `extras`), но поле
    `metadata: dict | None` на `BaseTool` существует и проставляется после сборки.
    """
    attrs = {
        META_IS_WRITE: is_write,
        META_MAIN_ONLY: main_only,
        META_FS_SCOPE: fs_scope,
        META_FEATURE: feature,
    }

    def _attach(built: BaseTool) -> BaseTool:
        # merge, чтобы не затереть metadata, если её уже выставил сам `tool(...)`.
        built.metadata = {**(built.metadata or {}), **attrs}
        return built

    # Bare-форма `@agent_tool` — name_or_callable это сама декорируемая функция.
    if callable(name_or_callable):
        return _attach(tool(name_or_callable, **tool_kwargs))

    # Параметризованная форма `@agent_tool(...)` / `@agent_tool("name", ...)` — возвращаем
    # декоратор. `tool(...)` без callable отдаёт декоратор, который мы применяем к функции.
    def decorator(func: Callable) -> BaseTool:
        if name_or_callable is None:
            built = tool(**tool_kwargs)(func)
        else:  # передано кастомное имя строкой
            built = tool(name_or_callable, **tool_kwargs)(func)
        return _attach(built)

    return decorator


# --- ридеры атрибутов (единая точка чтения metadata) --------------------------------
# Безопасны к тулам БЕЗ наших атрибутов (сторонние тулы в пуле, metadata=None) — отдают
# дефолт. `PermissionMiddleware` (2.3) читает `is_write` именно через `is_write_tool`.


def is_write_tool(t: BaseTool) -> bool:
    return bool((t.metadata or {}).get(META_IS_WRITE, False))


def is_main_only(t: BaseTool) -> bool:
    return bool((t.metadata or {}).get(META_MAIN_ONLY, False))


def fs_scope_of(t: BaseTool) -> str | None:
    return (t.metadata or {}).get(META_FS_SCOPE)


def feature_of(t: BaseTool) -> str | None:
    return (t.metadata or {}).get(META_FEATURE)


# --- сборка пула под агента ---------------------------------------------------------


def assemble_pool(
    full_pool: list[BaseTool],
    *,
    agent_scope: str,
    permission_mode: str | None = None,
    disabled_features: set[str] | frozenset[str] = frozenset(),
) -> list[BaseTool]:
    """Срез общего пула под агента — **вычитанием**, не whitelist'ом (П2).

    Из `full_pool` исключаем:
    - тулы отключённых подсистем (`feature` ∈ `disabled_features`) — проводка в пуле цела,
      активация гейтится флагом (напр. `RAG_ENABLED=false` → `disabled_features={"rag"}`);
    - `main_only`-тулы, если агент — субагент;
    - тулы, чья `fs_scope` вне зон, доступных скоупу агента (`None`-скоуп всегда проходит);
    - `is_write`-тулы, если `permission_mode == "plan"`.

    Про `permission_mode`: канонический per-turn фильтр write-тулов — `PermissionMiddleware`
    (2.3) в рантайме, потому что режим меняется от хода к ходу, а пул собирается реже.
    Здесь параметр опционален: передаётся, когда нужен корректный СТАРТОВЫЙ пул под режим
    (например, стартовый пул субагента). Дублирование намеренно; источник правды по режиму —
    middleware. `permission_mode=None` → write-тулы не вычитаем (сборка вне привязки к режиму).
    """
    allowed_zones = _FS_ZONES_BY_SCOPE.get(agent_scope, set())
    result: list[BaseTool] = []

    for t in full_pool:
        # 0) тул отключённой подсистемы (проводка цела, гейтится флагом)
        feat = feature_of(t)
        if feat is not None and feat in disabled_features:
            continue
        # 1) main_only вычитается у любого субагента
        if agent_scope == "subagent" and is_main_only(t):
            continue
        # 2) тул вне зоны ФС агента (None-скоуп — не привязан к ФС, всегда проходит)
        fs = fs_scope_of(t)
        if fs is not None and fs not in allowed_zones:
            continue
        # 3) write-тулы вне Act (только если режим передан)
        if permission_mode == "plan" and is_write_tool(t):
            continue
        result.append(t)

    return result
