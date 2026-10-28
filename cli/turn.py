"""Проведение одного хода через раннер AgentSession."""

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
    """Слушать esc и ctrl+c, пока идёт ход, и звать `on_cancel`.

    Терминал в raw-режиме: ctrl+c приходит клавишей, а не SIGINT, поэтому он отменяет
    ход, а не роняет CLI. На время HITL-вопроса слушатель снимают (`stop`), иначе он съест
    ввод ответа. Без терминала (пайп, тесты) — no-op.
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
        # flush_keys — чтобы одиночный esc не ждал продолжения escape-последовательности.
        keys = self._input.read_keys() + self._input.flush_keys()
        if any(k.key in (Keys.Escape, Keys.ControlC) for k in keys):
            self._on_cancel()


async def drive_turn(session: AgentSession, text: str, view: TurnView | None = None) -> None:
    """Один ход через раннер: запустить Task, дренажить события до turn_done, отвечать на HITL.

    HITL-событие перехватываем ДО рендера: пока ход припаркован на future, убираем спиннер,
    читаем ответ и резолвим — потребитель свободен, потому что ход крутится отдельной
    задачей (в этом вся суть раннера против прежнего прямого `async for` по run_stream).
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
                    view.note("ход прерван")
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
                    session.cancel_turn()  # ctrl+c/ctrl+d в вопросе — отмена всего хода
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
