"""
Общие FastAPI-зависимости session-scoped маршрутов.

`get_session` — единственная точка резолва session_id: неизвестный id → 404 ДО любой
интерполяции в путь ФС (id минтит только бэкенд — path-safe по построению).
`session_paths` зависит от `get_session`, поэтому порядок «сначала реестр, потом путь»
гарантирован самим DI, а не дисциплиной вызывающего.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, HTTPException, Request

from core.agent.session import AgentSession, SessionManager


def get_session_manager(request: Request) -> SessionManager:
    return request.app.state.sessions


def get_session(session_id: str, request: Request) -> AgentSession:
    """Сессия по id из реестра; неизвестный id → 404 (id минтит только бэкенд)."""
    session = request.app.state.sessions.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Unknown session_id")
    return session


def session_paths(
    session_id: str,
    request: Request,
    _session: AgentSession = Depends(get_session),
) -> tuple[Path, Path]:
    """(workspace, checkpoints) сессии. Зависимость от get_session гарантирует,
    что id проверен по реестру до интерполяции в путь."""
    session_dir = request.app.state.session_root / session_id
    return session_dir / "workspace", session_dir / "checkpoints"
