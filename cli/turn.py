"""Drive one turn through the AgentSession runner."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from prompt_toolkit.input import Input, create_input
from prompt_toolkit.keys import Keys

from cli.hitl import extract_hitl, handle_hitl
from cli.prompt import is_interactive
from cli.render import TurnView
from core.agent.session import AgentSession


class CancelKeys:
    """Listen for esc and ctrl+c during a turn and call `on_cancel`.

    The terminal is in raw mode: ctrl+c arrives as a key, not as SIGINT, so it cancels the
    turn instead of killing the CLI. The listener is removed (`stop`) for a HITL question,
    or it would swallow the answer. Without a terminal (a pipe, tests) this is a no-op.
    """

    def __init__(self, on_cancel: Callable[[], Any]) -> None:
        self._on_cancel = on_cancel
        self._input: Input | None = None
        self._contexts: list[Any] = []

    def start(self) -> None:
        if self._input is not None or not is_interactive():
            return
        self._input = create_input()
        for ctx in (self._input.raw_mode(), self._input.attach(self._on_keys)):
            ctx.__enter__()
            self._contexts.append(ctx)

    def stop(self) -> None:
        while self._contexts:
            self._contexts.pop().__exit__(None, None, None)
        if self._input is not None:
            self._input.close()
            self._input = None

    def _on_keys(self) -> None:
        if self._input is None:
            return
        # flush_keys so a lone esc does not wait for the rest of an escape sequence.
        keys = self._input.read_keys() + self._input.flush_keys()
        if any(k.key in (Keys.Escape, Keys.ControlC) for k in keys):
            self._on_cancel()


async def drive_turn(session: AgentSession, text: str, view: TurnView | None = None) -> None:
    """One turn through the runner: start the task, drain events until turn_done, answer HITL.

    A HITL event is caught before rendering: while the turn is parked on a future, drop the
    spinner, read the answer, and resolve it. The consumer stays free because the turn runs
    as its own task (that is the point of the runner versus a direct `async for` over
    run_stream).
    """
    view = view or TurnView()
    keys = CancelKeys(session.cancel_turn)
    session.run_turn(text)
    events = session.events()
    try:
        view.start()
        keys.start()
        async for ev in events:
            if ev.mode == "control":
                etype = (ev.data or {}).get("type")
                if etype == "turn_cancelled":
                    view.note("turn interrupted")
                elif etype == "turn_done":
                    return
                continue
            hitl = extract_hitl(ev)
            if hitl:
                keys.stop()
                view.pause()
                try:
                    await handle_hitl(session, hitl)
                except (KeyboardInterrupt, EOFError):
                    session.cancel_turn()  # ctrl+c/ctrl+d during a question cancels the whole turn
                finally:
                    view.resume()
                    keys.start()
                continue
            view.handle(ev)
    except Exception:
        # A failure on our side must not orphan the turn: its leftover events would be
        # read as the next turn's output.
        session.cancel_turn()
        async for ev in events:
            if ev.mode == "control" and (ev.data or {}).get("type") == "turn_done":
                break
        raise
    finally:
        keys.stop()
        view.stop()
