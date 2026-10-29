"""CLI answers to the agent's questions: the value handed to `resolve_hitl` per kind.

The menus themselves are prompt_toolkit's; here they are replaced by scripted answers,
so what is tested is the mapping from the user's pick to the tool contract.
"""

from __future__ import annotations

import pytest

from cli import hitl, prompt
from cli.config import state
from core.agent.base import StreamEvent

CONFIRM = {"type": "hitl_confirm", "hitl_id": "h1", "kind": "confirm", "action_description": "x"}


class FakeSession:
    def __init__(self) -> None:
        self.resolved: list[tuple[str, object]] = []
        self.decision_mode = "confirm"

    def resolve_hitl(self, hitl_id: str, value: object) -> None:
        self.resolved.append((hitl_id, value))


@pytest.fixture
def answers(monkeypatch):
    """Script the menus: `answers.choice` / `answers.text` are returned in order."""

    class Script:
        choice: list[object] = []
        text: list[str] = []
        menus: list[list[tuple[object, str]]] = []

    async def ask_choice(message, options):
        Script.menus.append(list(options))
        return Script.choice.pop(0)

    async def ask_text(message):
        return Script.text.pop(0)

    monkeypatch.setattr(prompt, "ask_choice", ask_choice)
    monkeypatch.setattr(prompt, "ask_text", ask_text)
    monkeypatch.setitem(state, "dec", "confirm")
    Script.choice, Script.text, Script.menus = [], [], []
    return Script


async def test_confirm_yes_and_no(answers):
    session = FakeSession()
    answers.choice = [True, False]
    confirm = CONFIRM
    await hitl.handle_hitl(session, confirm)
    await hitl.handle_hitl(session, {**confirm, "hitl_id": "h2"})
    assert session.resolved == [("h1", True), ("h2", False)]


async def test_confirm_always_switches_to_accept_and_stops_asking(answers):
    session = FakeSession()
    answers.choice = [hitl.ALWAYS]
    confirm = CONFIRM
    await hitl.handle_hitl(session, confirm)
    # The turn still runs in confirm mode, so the agent may ask again: answered silently.
    await hitl.handle_hitl(session, {**confirm, "hitl_id": "h2"})
    assert session.resolved == [("h1", True), ("h2", True)]
    assert state["dec"] == "accept_all"
    assert session.decision_mode == "accept_all"
    assert len(answers.menus) == 1


async def test_select_option_and_free_text(answers):
    session = FakeSession()
    question = {
        "type": "hitl_question",
        "hitl_id": "h1",
        "kind": "select",
        "question": "Какой файл?",
        "options": [{"id": "a", "label": "A"}, {"id": "b", "label": "B"}],
    }
    answers.choice = ["b", hitl.FREE_TEXT]
    answers.text = ["оба"]
    await hitl.handle_hitl(session, question)
    await hitl.handle_hitl(session, {**question, "hitl_id": "h2"})
    assert session.resolved == [
        ("h1", {"type": "selected", "id": "b"}),
        ("h2", {"type": "free_text", "value": "оба"}),
    ]
    assert [label for _, label in answers.menus[0]] == ["A", "B", "Your own answer…"]


async def test_select_without_free_text_has_no_extra_item(answers):
    session = FakeSession()
    answers.choice = ["a"]
    await hitl.handle_hitl(
        session,
        {
            "type": "hitl_question",
            "hitl_id": "h1",
            "kind": "select",
            "options": [{"id": "a", "label": "A"}],
            "allow_free_text": False,
        },
    )
    assert [label for _, label in answers.menus[0]] == ["A"]


async def test_ask_free_answer(answers):
    session = FakeSession()
    answers.text = ["42"]
    await hitl.handle_hitl(
        session, {"type": "hitl_question", "hitl_id": "h1", "kind": "ask", "question": "Цена?"}
    )
    assert session.resolved == [("h1", "42")]


def test_extract_hitl_unwraps_subagent_envelope():
    inner = {"type": "hitl_question", "hitl_id": "h1", "kind": "ask"}
    wrapped = {"type": "subagent_event", "mode": "custom", "data": inner}
    assert hitl.extract_hitl(StreamEvent("main", "custom", wrapped)) == inner
    assert hitl.extract_hitl(StreamEvent("main", "updates", inner)) is None
    assert hitl.extract_hitl(StreamEvent("main", "custom", {"type": "document_patch"})) is None
