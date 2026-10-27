"""HITL: перехват вопроса агента + конкурентный ответ.

Пока ход припаркован на future, читаем ответ и резолвим — потребитель свободен, потому что
ход крутится отдельной задачей (см. cli/turn.py и AgentSession). Подтверждение и выбор —
меню со стрелками (`cli.prompt.ask_choice`), свободный ответ — строка ввода.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text

from cli import prompt
from cli.config import state
from cli.render import console, shorten
from core.agent.session import AgentSession

FREE_TEXT = object()  # маркер пункта «свой ответ» в меню выбора
ALWAYS = "always"  # «да, и больше не спрашивать» в подтверждении


def _find_hitl(data: Any) -> dict | None:
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


def extract_hitl(ev: Any) -> dict | None:
    """HITL-payload из StreamEvent или None (HITL едет только custom-каналом)."""
    return _find_hitl(ev.data) if ev.mode == "custom" else None


def _question(text: str) -> None:
    console.print(Text.assemble(("❓ ", "yellow"), (text, "bold")))


async def handle_hitl(session: AgentSession, hitl: dict) -> None:
    """Показать вопрос агента, получить ответ, резолвнуть future → ход продолжится.

    Форма value повторяет контракт тула: ask → строка; confirm → bool; select →
    `{type: selected, id}` или `{type: free_text, value}`.
    """
    hitl_id = hitl["hitl_id"]
    kind = hitl.get("kind")

    if hitl.get("type") == "hitl_confirm" or kind == "confirm":
        description = shorten(hitl.get("action_description", ""), 300)
        if state["dec"] == "accept_all":
            # «Не спрашивать» выбрано раньше в этом же ходе: режим хода ещё confirm,
            # но пользователь уже разрешил — не дёргаем его повторно.
            console.print(Text(f"  ✓ подтверждено автоматически: {description}", style="dim"))
            session.resolve_hitl(hitl_id, True)
            return
        _question("Подтвердите действие")
        console.print(Text(f"  {description}", style="dim"))
        answer = await prompt.ask_choice(
            "Выполнить?",
            [(True, "Да"), (ALWAYS, "Да, и больше не спрашивать в этой сессии"), (False, "Нет")],
        )
        if answer == ALWAYS:
            state["dec"] = "accept_all"
            session.decision_mode = "accept_all"
            answer = True
        session.resolve_hitl(hitl_id, bool(answer))
        return

    if kind == "select":
        options = hitl.get("options") or []
        _question(hitl.get("question", ""))
        choices: list[tuple[object, str]] = [
            (opt.get("id"), str(opt.get("label"))) for opt in options
        ]
        if hitl.get("allow_free_text", True):
            choices.append((FREE_TEXT, "Свой ответ…"))
        picked = await prompt.ask_choice("Выберите вариант", choices)
        if picked is FREE_TEXT:
            text = await prompt.ask_text("ответ")
            session.resolve_hitl(hitl_id, {"type": "free_text", "value": text})
        else:
            session.resolve_hitl(hitl_id, {"type": "selected", "id": picked})
        return

    # ask (свободный ответ)
    _question(hitl.get("question", ""))
    session.resolve_hitl(hitl_id, await prompt.ask_text("ответ"))
