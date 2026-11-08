"""HITL: catch the agent's question and answer it while the turn is parked.

While the turn waits on a future, we read the answer and resolve it — the consumer is free
because the turn runs as its own task (see cli/turn.py and AgentSession). Confirm and
choice are arrow menus (`cli.prompt.ask_choice`); a free answer is a line of input.
"""

from __future__ import annotations

from typing import Any

from rich.text import Text

from cli import prompt
from cli.config import state
from cli.render import console, shorten
from core.agent.session import AgentSession

FREE_TEXT = object()  # marker for the "your own answer" menu item
ALWAYS = "always"  # "yes, and don't ask again" in a confirmation


def _find_hitl(data: Any) -> dict | None:
    """Pull the HITL payload out of a custom event, unwrapping one subagent envelope.

    Main HITL arrives as `{type: hitl_question|hitl_confirm, ...}`; a subagent's is wrapped
    by the delegation multiplex in `{type: subagent_event, mode: custom, data: <hitl>}`.
    The hierarchy is one level deep.
    """
    if not isinstance(data, dict):
        return None
    if data.get("type") in ("hitl_question", "hitl_confirm"):
        return data
    if data.get("type") == "subagent_event" and data.get("mode") == "custom":
        return _find_hitl(data.get("data"))
    return None


def extract_hitl(ev: Any) -> dict | None:
    """HITL payload from a StreamEvent, or None (HITL travels only on the custom channel)."""
    return _find_hitl(ev.data) if ev.mode == "custom" else None


def _question(text: str) -> None:
    console.print(Text.assemble(("❓ ", "yellow"), (text, "bold")))


async def handle_hitl(session: AgentSession, hitl: dict) -> None:
    """Show the agent's question, read the answer, resolve the future so the turn continues.

    The value matches the tool contract: ask → a string; confirm → a bool; select →
    `{type: selected, id}` or `{type: free_text, value}`.
    """
    hitl_id = hitl["hitl_id"]
    kind = hitl.get("kind")

    if hitl.get("type") == "hitl_confirm" or kind == "confirm":
        description = shorten(hitl.get("action_description", ""), 300)
        if state["dec"] == "accept_all":
            # "Don't ask again" was chosen earlier in this same turn: the turn is still in
            # confirm mode, but the user already allowed it — don't ask a second time.
            console.print(Text(f"  ✓ confirmed automatically: {description}", style="dim"))
            session.resolve_hitl(hitl_id, True)
            return
        _question("Confirm the action")
        console.print(Text(f"  {description}", style="dim"))
        answer = await prompt.ask_choice(
            "Run it?",
            [(True, "Yes"), (ALWAYS, "Yes, and don't ask again"), (False, "No")],
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
            choices.append((FREE_TEXT, "Your own answer…"))
        picked = await prompt.ask_choice("Choose an option", choices)
        if picked is FREE_TEXT:
            text = await prompt.ask_text("answer")
            session.resolve_hitl(hitl_id, {"type": "free_text", "value": text})
        else:
            session.resolve_hitl(hitl_id, {"type": "selected", "id": picked})
        return

    # ask (free text)
    _question(hitl.get("question", ""))
    session.resolve_hitl(hitl_id, await prompt.ask_text("answer"))
