"""Dump the tools as they are sent to the model (OpenAI function-calling wire format).

A tool for the eyes: shows exactly what the agent "sees" about each tool — name, description
(= the docstring verbatim) and the JSON schema of the arguments. The function body, `#`
comments and error texts do NOT go into the payload (the model sees errors only as a runtime
ToolMessage on a miss).

Uses the same FULL_POOL as the CLI (core.agent.pool), so a new tool shows up here
automatically. Can apply assemble_pool (a slice for scope/mode) and filter by name.

Examples:
    python scripts/dump_tool_schemas.py                       # the whole pool
    python scripts/dump_tool_schemas.py -n read grep
    python scripts/dump_tool_schemas.py --scope subagent --mode plan
    python scripts/dump_tool_schemas.py --names-only          # names + flags only
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
    """The Windows console defaults to cp1251 and chokes on Cyrillic/arrows — force UTF-8."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream.encoding and stream.encoding.lower().startswith("utf"):
            continue
        setattr(sys, name, io.TextIOWrapper(stream.buffer, encoding="utf-8"))


def _select_pool(scope: str | None, mode: str | None) -> list[BaseTool]:
    """FULL_POOL as is, or an assemble_pool slice for scope/mode (what a given agent sees)."""
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
