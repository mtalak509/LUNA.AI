"""Дамп тулов в том виде, в каком они уходят модели (OpenAI function-calling wire-формат).

Утилита для глаз: показывает, что именно «видит» агент про каждый тул — имя, description
(= docstring дословно) и JSON-схему аргументов. Тело функции, `#`-комментарии и тексты
ошибок в payload НЕ попадают (ошибки видны модели только рантайм-ToolMessage'ом при промахе).

Берёт тот же FULL_POOL, что и CLI (core.agent.pool), поэтому новый тул появляется
здесь автоматически. Можно применить assemble_pool (срез под scope/mode) и сузить по имени.

Примеры:
    python scripts/dump_tool_schemas.py                       # весь пул
    python scripts/dump_tool_schemas.py -n json_inspect json_search
    python scripts/dump_tool_schemas.py --scope subagent --mode plan
    python scripts/dump_tool_schemas.py --names-only          # только имена + флаги
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

# Запуск файла из scripts/ кладёт на sys.path сам scripts/, а не корень проекта —
# добавляем корень явно на случай запуска без установленного пакета.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool

from core.agent.pool import FULL_POOL
from core.agent.tools import assemble_pool, fs_scope_of, is_main_only, is_write_tool


def _force_utf8_io() -> None:
    """Windows-консоль по умолчанию cp1251 и давится на кириллице/стрелках — форсим UTF-8."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream.encoding and stream.encoding.lower().startswith("utf"):
            continue
        setattr(sys, name, io.TextIOWrapper(stream.buffer, encoding="utf-8"))


def _select_pool(scope: str | None, mode: str | None) -> list[BaseTool]:
    """FULL_POOL как есть, либо срез assemble_pool под scope/mode (как видит конкретный агент)."""
    if scope is None:
        return list(FULL_POOL)
    return assemble_pool(FULL_POOL, agent_scope=scope, permission_mode=mode)


def _filter_by_name(pool: list[BaseTool], names: list[str] | None) -> list[BaseTool]:
    if not names:
        return pool
    wanted = set(names)
    selected = [t for t in pool if t.name in wanted]
    missing = wanted - {t.name for t in selected}
    if missing:
        print(f"# тулы не найдены в пуле: {', '.join(sorted(missing))}", file=sys.stderr)
    return selected


def _flags(t: BaseTool) -> str:
    parts = []
    if is_write_tool(t):
        parts.append("write")
    if is_main_only(t):
        parts.append("main_only")
    fs = fs_scope_of(t)
    parts.append(f"fs={fs}")
    return " ".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-n", "--name", dest="names", nargs="+", help="сузить до конкретных тулов по имени")
    parser.add_argument("--scope", choices=["main", "subagent"], help="применить assemble_pool под scope агента")
    parser.add_argument("--mode", choices=["plan", "act"], help="permission_mode для среза (только со --scope)")
    parser.add_argument("--names-only", action="store_true", help="только имена + декларативные флаги, без схемы")
    args = parser.parse_args()

    _force_utf8_io()

    pool = _filter_by_name(_select_pool(args.scope, args.mode), args.names)
    if not pool:
        print("# пул пуст", file=sys.stderr)
        sys.exit(1)

    if args.names_only:
        for t in pool:
            print(f"{t.name:32} {_flags(t)}")
        print(f"\n# всего тулов: {len(pool)}", file=sys.stderr)
        return

    payload = [convert_to_openai_tool(t) for t in pool]
    out = json.dumps(payload, ensure_ascii=False, indent=2)
    print(out)

    # Грубая оценка веса (символы ≈ /4 токена) — это уходит модели КАЖДЫЙ ход.
    chars = len(out)
    print(
        f"\n# тулов: {len(pool)}, символов в payload: {chars}, ~токенов: {chars // 4}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
