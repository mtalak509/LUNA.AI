"""HITL: перехват вопроса агента + конкурентный ответ.

Пока ход припаркован на future, читаем ввод в отдельном потоке и резолвим — потребитель
свободен, потому что ход крутится отдельной задачей (см. cli/turn.py и AgentSession).
"""

from __future__ import annotations

import asyncio

from cli.render import yellow
from core.agent.session import AgentSession


def _find_hitl(data) -> dict | None:
    """Достать HITL-payload из custom-события, разворачивая обёртку субагента на 1 уровень.

    Main-HITL приходит как `{type: hitl_question|hitl_confirm, ...}`; субагентский — обёрнут
    мультиплексом делегации в `{type: subagent_event, mode: custom, data: <hitl>}`.
    Иерархия одноуровневая — глубже одного разворота не бывает.
    """
    if not isinstance(data, dict):
        return None
    if data.get("type") in ("hitl_question", "hitl_confirm"):
        return data
    if data.get("type") == "subagent_event" and data.get("mode") == "custom":
        return _find_hitl(data.get("data"))
    return None


def extract_hitl(ev) -> dict | None:
    """HITL-payload из StreamEvent или None (HITL едет только custom-каналом)."""
    return _find_hitl(ev.data) if ev.mode == "custom" else None


async def _ask(prompt: str) -> str:
    """Прочитать строку, не блокируя event loop (ход припаркован на future в это время)."""
    return (await asyncio.to_thread(input, prompt)).strip()


async def handle_hitl(session: AgentSession, hitl: dict) -> None:
    """Показать вопрос агента, прочитать ответ, резолвнуть future → ход продолжится.

    Форма value повторяет контракт тула: ask → строка; confirm → bool; select →
    `{type: selected, id}` или `{type: free_text, value}`.
    """
    hitl_id = hitl["hitl_id"]
    kind = hitl.get("kind")

    if hitl.get("type") == "hitl_confirm" or kind == "confirm":
        print(yellow(f"\n  ❓ подтвердите: {hitl.get('action_description', '')}  [y/n]"))
        ans = (await _ask("  ответ > ")).lower()
        session.resolve_hitl(hitl_id, ans in ("y", "yes", "д", "да", "1", "true"))
        return

    if kind == "select":
        options = hitl.get("options") or []
        allow_free = hitl.get("allow_free_text", True)
        print(yellow(f"\n  ❓ {hitl.get('question', '')}"))
        for i, opt in enumerate(options):
            print(yellow(f"    [{i}] {opt.get('label')}  (id={opt.get('id')})"))
        hint = "номер или текст" if allow_free else "номер"
        while True:
            raw = await _ask(f"  выбор ({hint}) > ")
            if raw.isdigit() and int(raw) < len(options):
                session.resolve_hitl(hitl_id, {"type": "selected", "id": options[int(raw)]["id"]})
                return
            if allow_free:
                session.resolve_hitl(hitl_id, {"type": "free_text", "value": raw})
                return
            print(yellow("    нужен номер варианта"))

    # ask (свободный ответ)
    print(yellow(f"\n  ❓ {hitl.get('question', '')}"))
    session.resolve_hitl(hitl_id, await _ask("  ответ > "))
