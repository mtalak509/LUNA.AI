"""
Низкоуровневые хелперы работы с JSON-документами для json_*-тулов.
Здесь сосредоточена вся работа с JSON Pointer (RFC 6901) и JSON Patch (RFC 6902),
загрузка/сохранение документа и рендер «карты одного уровня» для json_inspect. Тулы
(json_tools.py) остаются тонкими: резолв зоны через PathScope + вызов этих функций.

Все штатные отказы выражаются доменной JsonError(ToolException) — её ловит
ToolErrorMiddleware и отдаёт модели как ToolMessage(status="error") для self-correction.
Сырые исключения jsonpatch/jsonpointer наружу не всплывают — оборачиваются здесь с
понятным текстом (текст ошибки = контракт self-correction).
"""

from __future__ import annotations

import json, os
import jsonpointer
import jsonpatch

from pathlib import Path
from typing import Any
from jsonpointer import JsonPointerException
from jsonpatch import JsonPatchException
from langchain_core.tools import ToolException


class JsonError(ToolException):
    """Штатный отказ json_*-тула (нет файла / битый JSON / промах указателя / невалидный патч)."""


_SNIPPET_MAX_LEN: int | None = None  # обрезка сниппета значения в json_search; None = не обрезаем


# --- загрузка/сохранение документа ---------------------------------------------------


def load_doc(path: Path) -> Any:
    """Загрузить JSON-документ из файла по пути."""
    if not path.is_file():
        raise JsonError(f"JSON-документ не найден: {path.name}")
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise JsonError(f"не удалось прочитать JSON-документ: {e}") from e
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise JsonError(f"невалидный JSON в документе: {e}") from e


def save_doc(path: Path, data: Any) -> None:
    """Атомарно записать в документ (tmp в том же каталоге + os.replace)."""
    text = json.dumps(data, ensure_ascii=False, indent=2)
    tmp_path = path.with_name(path.name + ".tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, path)
    except OSError as e:
        raise JsonError(f"не удалось записать JSON-документ: {e}") from e
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


# --- JSON Pointer (RFC 6901) --------------------------------------------------------


def get_node(doc: Any, pointer: str) -> Any:
    """Резолв JSON Pointer (RFC 6901). Пустой '' → весь документ; промах → JsonError."""
    try:
        return jsonpointer.resolve_pointer(doc, pointer)
    except JsonPointerException as e:
        raise JsonError(f"указатель не существует: {pointer!r}") from e


def apply_ops(doc: Any, operations: list[dict]) -> Any:
    """Применить RFC 6902 Patch к КОПИИ документа и вернуть результат.

    Транзакционно: оригинал не мутируется (in_place=False), любая ошибка операции →
    JsonError, наружу уходит неизменённый документ. Пустой список отклоняем,
    чтобы json_patch не записал идентичный документ и не снял лишний чекпоинт.
    """
    if not operations:
        raise JsonError("пустой список операций")
    try:
        return jsonpatch.apply_patch(doc, operations, in_place=False)
    except (JsonPatchException, JsonPointerException, TypeError) as e:
        raise JsonError(f"не удалось применить патч: {e}") from e

# --- рендер / поиск -----------------------------------------------------------------


def _summarize(value: Any) -> str:
    """Однострочная сводка узла: скаляр — целиком, ветка — тип + размер (НЕ разворачивается)."""
    if isinstance(value, dict):
        return f"object: {len(value)} ключей"
    if isinstance(value, list):
        return f"array: {len(value)} элементов"
    return json.dumps(value, ensure_ascii=False)


def _snippet(value: Any) -> str:
    text = _summarize(value)
    if _SNIPPET_MAX_LEN is not None and len(text) > _SNIPPET_MAX_LEN:
        return text[:_SNIPPET_MAX_LEN] + "…"
    return text


def render_level(node: Any, *, max_children: int | None = None) -> str:
    """Карта ОДНОГО уровня узла (для json_inspect): листья целиком, ветки сводкой.

    Ответ ограничен арностью узла, а не размером поддерева — рекурсии нет специально
    (защита контекста от широких/глубоких узлов). max_children=None → показываем всех.
    """
    if not isinstance(node, (dict, list)):
        return json.dumps(node, ensure_ascii=False)  # скаляр по указателю

    if isinstance(node, dict):
        items = [f'"{k}": {_summarize(v)}' for k, v in node.items()]
    else:
        items = [f"[{i}]: {_summarize(v)}" for i, v in enumerate(node)]

    if not items:
        return "(пусто)"
    if max_children is not None and len(items) > max_children:
        hidden = len(items) - max_children
        items = items[:max_children] + [f"… ещё {hidden}"]
    return "\n".join(items)


def lexical_search(
    doc: Any, query: str, *, path_hint: str = "", max_results: int | None = None
) -> list[str]:
    """Лексический поиск по ключам И значениям → JSON Pointer'ы попаданий.

    Чистая функция (пустой query валидирует вызывающий тул). Обход от path_hint-узла;
    указатели собираются с экранированием сегментов. Совпадение по ключу и по скалярному
    значению (case-insensitive substring). Возврат — строки '<pointer>: <сниппет>'.
    """
    needle = query.lower()
    root = get_node(doc, path_hint)
    base = path_hint.rstrip("/") if path_hint else ""
    hits: list[str] = []

    def _capped() -> bool:
        return max_results is not None and len(hits) >= max_results

    def walk(node: Any, pointer: str) -> None:
        if _capped():
            return
        if isinstance(node, dict):
            for k, v in node.items():
                if _capped():
                    return
                child = f"{pointer}/{jsonpointer.escape(k)}"
                if needle in k.lower():
                    hits.append(f"{child}: {_snippet(v)}")
                walk(v, child)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                if _capped():
                    return
                walk(v, f"{pointer}/{i}")
        else:  # скаляр-лист
            if needle in str(node).lower():
                hits.append(f"{pointer}: {_snippet(node)}")

    walk(root, base)
    return hits
