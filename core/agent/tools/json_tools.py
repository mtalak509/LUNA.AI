"""3 смарт-обёртки над json_doc-хелперами для работы с JSON-документами по пути.

json_inspect/json_search — чтение (структурная навигация без заливки всего файла в контекст),
json_patch — транзакционная запись через RFC 6902 JSON Patch. Все три принимают путь к
JSON-файлу в рабочей зоне агента (резолв через PathScope, как у файловых тулов)."""

from __future__ import annotations

import logging

from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime

from core.agent.checkpoint import with_checkpoint
from core.agent.tools import agent_tool
from core.agent.tools.fs_paths import PathScope
from core.agent.tools import json_doc as jd

_logger = logging.getLogger(__name__)


def _scope() -> PathScope:
    """Получить PathScope для текущего агента из рантайм-контекста."""
    runtime_context = get_runtime().context
    return PathScope(
        agent_scope=runtime_context.agent_scope,
        workspace_path=runtime_context.workspace_path,
        subagent_path=runtime_context.subagent_path,
    )


# --- только чтение (is_write=False) ---------------------------------------------------------------------


@agent_tool(is_write=False)
async def json_inspect(file: str, pointer: str = "", max_children: int | None = None) -> str:
    """Показать структуру JSON-документа на ОДИН уровень вглубь по JSON Pointer.

    Инструмент навигации по большому JSON без заливки его целиком в контекст: возвращает
    карту указанного узла — скалярные поля целиком, вложенные объекты/массивы свёрнуты в
    сводку (`object: N ключей` / `array: N элементов`), без рекурсии. Чтобы заглянуть
    глубже — вызови снова с указателем на нужного ребёнка.

    file — относительный путь к JSON-файлу в рабочей зоне (напр. 'data/report.json').
    pointer — JSON Pointer (RFC 6901) ВНУТРИ документа, НЕ путь файла:
        ""                     — корень (верхний уровень документа)
        "/items"               — поле items верхнего уровня
        "/items/0/name"        — поле name нулевого элемента массива items
    """
    doc = jd.load_doc(_scope().resolve_read(file))
    node = jd.get_node(doc, pointer)
    return jd.render_level(node, max_children=max_children)


@agent_tool(is_write=False)
async def json_search(
    file: str, query: str, path_hint: str = "", max_results: int | None = None
) -> str:
    """Найти, ГДЕ в JSON-документе встречается подстрока (по ключам и значениям).

    Совпадение — подстрока без учёта регистра (не семантический поиск). Возвращает по
    строке на хит: `<JSON Pointer>: <значение>`. Передай найденный указатель в json_inspect
    (изучить) или json_patch (изменить). Нет совпадений → "(ничего не найдено)".

    file — относительный путь к JSON-файлу в рабочей зоне.
    path_hint — JSON Pointer, сужающий поиск поддеревом ("" = весь документ).
    """
    if query.strip() == "":
        raise jd.JsonError("пустой запрос")
    doc = jd.load_doc(_scope().resolve_read(file))
    hits = jd.lexical_search(doc, query, path_hint=path_hint, max_results=max_results)
    return "\n".join(hits) if hits else "(ничего не найдено)"


# --- запись (is_write=True, триггерит чекпоинт) --------------------------------------------------------


@agent_tool(is_write=True)
@with_checkpoint()
async def json_patch(file: str, operations: list[dict]) -> str:
    """Изменить JSON-документ набором операций RFC 6902 JSON Patch.

    Точечная транзакционная правка JSON: пути операций — JSON Pointer (используй
    json_inspect/json_search, чтобы узнать точные указатели). Поддержаны операции
    add, remove, replace, move, copy, test. Копирование узла:
    {"op": "copy", "from": "/src", "path": "/dst"}.

    Патч транзакционен: при ошибке любой операции документ остаётся прежним — исправь и повтори.

    file — относительный путь к JSON-файлу в рабочей зоне.
    operations — список операций, напр.:
        [{"op": "replace", "path": "/title", "value": "Отчёт-2"}]
    """
    p = _scope().resolve_write(file)
    doc = jd.load_doc(p)
    new_doc = jd.apply_ops(doc, operations)  # ошибка op → JsonError ДО записи
    jd.save_doc(p, new_doc)
    _emit_document_patch(file, operations)
    return f"Применено операций: {len(operations)}"


def _emit_document_patch(file: str, operations: list[dict]) -> None:
    """Эмит document_patch в custom-стрим для live-preview UI. Best-effort — не роняет тул.

    Запись уже состоялась: сбой стрима (или вызов вне consumer'а) не должен превратить
    успешный патч в tool-error, иначе модель ретайльнёт неидемпотентную операцию. Та же
    логика, что у _try_snapshot в checkpoint.py.
    """
    try:
        get_stream_writer()({"type": "document_patch", "file": file, "operations": operations})
    except Exception:  # noqa: BLE001 — стрим-сбой не маскирует уже состоявшуюся запись
        _logger.debug("document_patch emit skipped", exc_info=True)
