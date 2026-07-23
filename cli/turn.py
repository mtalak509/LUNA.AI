"""Проведение одного хода через раннер AgentSession."""

from __future__ import annotations

from cli.config import state
from cli.hitl import extract_hitl, handle_hitl
from cli.render import render, yellow
from core.agent.session import AgentSession


async def drive_turn(session: AgentSession, text: str) -> None:
    """Один ход через раннер: запустить Task, дренажить события до turn_done, отвечать на HITL.

    HITL-событие перехватываем ДО render'а: пока ход припаркован на future, читаем ответ и
    резолвим — потребитель свободен, потому что ход крутится отдельной задачей (в этом вся
    суть раннера против прежнего прямого `async for` по run_stream).
    """
    session.run_turn(text, pointer=state["ptr"])
    async for ev in session.events():
        if ev.mode == "control":
            etype = (ev.data or {}).get("type")
            if etype == "turn_cancelled":
                print(yellow("\n[ход отменён]"))
            elif etype == "turn_done":
                return
            continue
        hitl = extract_hitl(ev)
        if hitl:
            await handle_hitl(session, hitl)
            continue
        render(ev)
