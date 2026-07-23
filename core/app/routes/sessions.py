"""Жизненный цикл сессий + процессный /health."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response

from core.agent.session import SessionManager
from core.agent.tools._hitl import get_hitl_registry
from core.models import SessionCreateRequest

from ._deps import get_session_manager

router = APIRouter()


@router.get("/health")
async def health_check(sessions: SessionManager = Depends(get_session_manager)) -> dict:
    """Процессная живость: суммарный pending_hitl по всем сессиям + счётчик сессий.

    Per-session факты (has_document, режимы) — в GET /sessions.
    """
    try:
        pending = len(get_hitl_registry().pending_ids())
    except Exception:
        pending = 0
    return {
        "status": "ok",
        "pending_hitl": pending,
        "sessions_count": len(sessions.list_sessions()),
    }


@router.post("/sessions", status_code=201)
async def create_session(
    body: SessionCreateRequest,
    sessions: SessionManager = Depends(get_session_manager)
    ) -> dict:
    """Создать сессию с указанным профилем."""
    #TODO: MAX_SESSIONS guard → 429.
    session_id, _ = sessions.create_session(body.profile)
    return {"session_id": session_id, "profile": body.profile}


@router.get("/sessions")
async def list_sessions(sessions: SessionManager = Depends(get_session_manager)) -> list[dict]:
    """Список сессий процесса: busy, режимы, has_document, метки активности."""
    return sessions.list_sessions()


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: str, sessions: SessionManager = Depends(get_session_manager)
) -> Response:
    """Ручная чистка: гасит активный ход, удаляет сессию из реестра и с диска."""
    try:
        await sessions.delete_session(session_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail="Unknown session_id") from e
    return Response(status_code=204)
