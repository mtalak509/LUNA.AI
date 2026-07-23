"""Session-scoped маршруты хода: turn / events (SSE) / hitl / stop / mode."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from core.agent.session import AgentSession
from core.app.sse import _sse_stream
from core.agent.tools._hitl import HitlError
from core.models import HitlRespondRequest, ModeRequest, ModeResponse, TurnRequest

from ._deps import get_session

router = APIRouter()


@router.post("/sessions/{session_id}/turn", status_code=202)
async def turn(body: TurnRequest, session: AgentSession = Depends(get_session)) -> dict:
    """Запустить ход агента в фоне; вывод — через GET /events. 409 если сессия занята."""
    try:
        turn_id = session.run_turn(body.text, body.pointer)
    except RuntimeError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return {"turn_id": turn_id}


@router.post("/sessions/{session_id}/hitl/respond")
async def hitl_respond(
    body: HitlRespondRequest, session: AgentSession = Depends(get_session)
) -> dict:
    """Ответить на висящий HITL-вопрос; разблокирует припаркованный тул хода."""
    try:
        session.resolve_hitl(body.hitl_id, body.value)
    except HitlError as e:  # неизвестный ИЛИ чужой hitl_id — неразличимы (15.3)
        raise HTTPException(status_code=404, detail=str(e)) from e
    return {"resolved": body.hitl_id}


@router.post("/sessions/{session_id}/stop")
async def stop(session: AgentSession = Depends(get_session)) -> dict:
    """Отменить активный ход сессии (включая субагента)."""
    return {"cancelled": session.cancel_turn()}


@router.get("/sessions/{session_id}/mode", response_model=ModeResponse)
async def get_mode(session: AgentSession = Depends(get_session)) -> ModeResponse:
    """Текущие permission_mode и decision_mode сессии."""
    return ModeResponse(
        permission_mode=session.permission_mode,
        decision_mode=session.decision_mode,
    )


@router.post("/sessions/{session_id}/mode", response_model=ModeResponse)
async def set_mode(
    body: ModeRequest, session: AgentSession = Depends(get_session)
) -> ModeResponse:
    """Обновить режимы для следующих ходов.

    Менять режим можно и во время активного хода: текущий ход продолжит работу с
    режимами, прочитанными при старте, а новые значения применятся со следующего хода.
    """
    if body.permission_mode is not None:
        session.permission_mode = body.permission_mode
    if body.decision_mode is not None:
        session.decision_mode = body.decision_mode
    return ModeResponse(
        permission_mode=session.permission_mode,
        decision_mode=session.decision_mode,
    )


@router.get("/sessions/{session_id}/events")
async def events(session: AgentSession = Depends(get_session)) -> StreamingResponse:
    """SSE-стрим событий сессии: один поток на весь сеанс, переживает много ходов."""
    return StreamingResponse(
        _sse_stream(session),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
