"""Диагностика ChatOpenAI-клиента с патчем reasoning-дельт.

Собирает модель через `build_chat_model` (импорт `core.agent.base` ставит патч
конвертера из `core/agent/patches/`), гоняет один запрос и показывает, какие поля
отдаёт ChatOpenAI:

- стрим `astream`: компактная строка на каждый чанк (типы content-блоков, дельты
  текста/reasoning, tool_call_chunks); `--raw` — полный `model_dump()` каждого чанка;
- агрегат стрима (сумма чанков) — полный дамп: `content`, `content_blocks`,
  `response_metadata`, `usage_metadata`, `tool_calls`;
- `--invoke` — дополнительно нестриминговый `ainvoke` (эталон формы reasoning-блока).

    python scripts/inspect_chat_model.py
    python scripts/inspect_chat_model.py --raw --prompt "Сколько будет 2+2?"
    python scripts/inspect_chat_model.py --invoke
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# Запуск как скрипта: корень репо в sys.path, чтобы работали импорты `core.*`.
sys.path.insert(0, str(Path(__file__).parents[1]))

from langchain_core.messages import BaseMessage, HumanMessage  # noqa: E402

from core.agent.base import build_chat_model  # noqa: E402  (импорт ставит патч)
from core.config import get_config  # noqa: E402

DEFAULT_PROMPT = "Сколько будет 17*23? Ответь одним числом."


def _dump(obj: object) -> str:
    """JSON с отступами; не-JSON-типы деградируют в str (как SSE-сериализация)."""
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)


def _delta_line(i: int, chunk: BaseMessage) -> str:
    """Компактная строка одного чанка: типы блоков + содержимое дельт."""
    parts: list[str] = []
    content = chunk.content if isinstance(chunk.content, list) else [chunk.content]
    for block in content:
        if not isinstance(block, dict):
            parts.append(f"str={block!r}")
        elif block.get("type") == "text":
            parts.append(f"text={block.get('text')!r}")
        elif block.get("type") == "reasoning":
            texts = "".join(
                p.get("text") or "" for p in block.get("content") or [] if isinstance(p, dict)
            )
            parts.append(f"reasoning={texts!r}")
        else:
            parts.append(f"{block.get('type')}={ {k: v for k, v in block.items() if k != 'type'} }")
    for tc in getattr(chunk, "tool_call_chunks", None) or []:
        parts.append(f"tool_call_chunk(name={tc.get('name')!r}, args={tc.get('args')!r})")
    if chunk.response_metadata:
        parts.append(f"meta_keys={sorted(chunk.response_metadata)}")
    if chunk.usage_metadata:
        parts.append(f"usage={chunk.usage_metadata}")
    return f"[{i:4d}] " + " | ".join(parts or ["<пустой чанк>"])


def _print_message_fields(title: str, msg: BaseMessage) -> None:
    print(f"\n{'=' * 80}\n{title}\n{'=' * 80}")
    print(f"\n-- type: {msg.type}   id: {msg.id}")
    print(f"\n-- content ({type(msg.content).__name__}):\n{_dump(msg.content)}")
    print(f"\n-- content_blocks (стандартизованный вид):\n{_dump(msg.content_blocks)}")
    print(f"\n-- text (свойство .text):\n{msg.text!r}")
    if getattr(msg, "tool_calls", None):
        print(f"\n-- tool_calls:\n{_dump(msg.tool_calls)}")
    print(f"\n-- response_metadata:\n{_dump(msg.response_metadata)}")
    print(f"\n-- usage_metadata:\n{_dump(getattr(msg, 'usage_metadata', None))}")
    if msg.additional_kwargs:
        print(f"\n-- additional_kwargs:\n{_dump(msg.additional_kwargs)}")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="текст запроса модели")
    parser.add_argument("--raw", action="store_true", help="полный model_dump() каждого чанка")
    parser.add_argument("--invoke", action="store_true", help="дополнительно нестриминговый ainvoke")
    args = parser.parse_args()

    cfg = get_config()
    model = build_chat_model(cfg)
    print(f"model={cfg.gpustack.llm_model}  base_url={cfg.gpustack.base_url}")
    print(f"prompt={args.prompt!r}")

    messages = [HumanMessage(args.prompt)]

    print(f"\n{'=' * 80}\nСТРИМ astream: чанки по мере прихода\n{'=' * 80}")
    aggregate = None
    async for i_chunk in _enumerate_aiter(model.astream(messages)):
        i, chunk = i_chunk
        if args.raw:
            print(f"\n[{i:4d}] {_dump(chunk.model_dump())}")
        else:
            print(_delta_line(i, chunk))
        aggregate = chunk if aggregate is None else aggregate + chunk

    if aggregate is not None:
        _print_message_fields("АГРЕГАТ СТРИМА (сумма чанков — то, что уедет в updates)", aggregate)

    if args.invoke:
        msg = await model.ainvoke(messages)
        _print_message_fields("НЕСТРИМИНГОВЫЙ ainvoke (эталон формы)", msg)


async def _enumerate_aiter(ait):
    i = 0
    async for item in ait:
        yield i, item
        i += 1


if __name__ == "__main__":
    # Windows-консоль по умолчанию не UTF-8 — иначе кириллица в дампах ломается.
    sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
