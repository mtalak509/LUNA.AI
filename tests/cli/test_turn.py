"""`drive_turn`: drains the runner until `turn_done`, answers HITL in between."""

from __future__ import annotations

import asyncio
from io import StringIO

from langchain_core.messages import AIMessageChunk
from rich.console import Console

from cli import hitl, turn
from cli.render import TurnView
from core.agent.base import StreamEvent


class ScriptedSession:
    """Plays fixed events; a HITL question parks until `resolve_hitl` is called."""

    def __init__(self, events: list[StreamEvent]) -> None:
        self._events = events
        self.resolved: list[tuple[str, object]] = []
        self.cancelled = False
        self.text: str | None = None
        self._answered = asyncio.Event()

    def run_turn(self, text: str) -> str:
        self.text = text
        return "turn-1"

    def resolve_hitl(self, hitl_id: str, value: object) -> None:
        self.resolved.append((hitl_id, value))
        self._answered.set()

    def cancel_turn(self) -> bool:
        self.cancelled = True
        return True

    async def events(self):
        for ev in self._events:
            yield ev
            if hitl.extract_hitl(ev):
                await self._answered.wait()


def _view() -> tuple[TurnView, Console]:
    out = Console(file=StringIO(), width=100, record=True, color_system=None)
    return TurnView(out), out


def _done() -> StreamEvent:
    return StreamEvent("main", "control", {"type": "turn_done", "turn_id": "turn-1"})


async def test_turn_renders_until_done_and_ignores_later_events():
    session = ScriptedSession(
        [
            StreamEvent("main", "messages", (AIMessageChunk(content="готово"), {})),
            _done(),
            StreamEvent("main", "messages", (AIMessageChunk(content="лишнее"), {})),
        ]
    )
    view, out = _view()
    await turn.drive_turn(session, "привет", view)
    text = out.export_text()
    assert session.text == "привет"
    assert "готово" in text and "лишнее" not in text


async def test_turn_answers_hitl_and_continues(monkeypatch):
    async def ask_text(message):
        return "42"

    monkeypatch.setattr(hitl.prompt, "ask_text", ask_text)
    question = {"type": "hitl_question", "hitl_id": "h1", "kind": "ask", "question": "Цена?"}
    session = ScriptedSession(
        [
            StreamEvent("main", "custom", question),
            StreamEvent("main", "messages", (AIMessageChunk(content="поменяла"), {})),
            _done(),
        ]
    )
    view, out = _view()
    await turn.drive_turn(session, "x", view)
    assert session.resolved == [("h1", "42")]
    assert "поменяла" in out.export_text()


async def test_ctrl_c_in_question_cancels_the_turn(monkeypatch):
    async def ask_text(message):
        raise KeyboardInterrupt

    monkeypatch.setattr(hitl.prompt, "ask_text", ask_text)
    question = {"type": "hitl_question", "hitl_id": "h1", "kind": "ask", "question": "?"}
    session = ScriptedSession(
        [
            StreamEvent("main", "custom", question),
            StreamEvent("main", "control", {"type": "turn_cancelled", "turn_id": "turn-1"}),
            _done(),
        ]
    )
    session._answered.set()  # отмена не резолвит future — не ждём ответа
    view, out = _view()
    await turn.drive_turn(session, "x", view)
    assert session.cancelled
    assert "ход прерван" in out.export_text()
