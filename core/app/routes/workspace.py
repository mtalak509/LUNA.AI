"""История сессии."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends

from core.agent.messages import MessageStore

from ._deps import session_paths

router = APIRouter()


@router.get("/sessions/{session_id}/history")
async def get_history(paths: tuple[Path, Path] = Depends(session_paths)) -> dict:
    """Получить историю сессии в формате `messages_to_dict` (`{type, data}`)."""
    workspace_path, _ = paths
    return MessageStore(workspace_path).load_ui_messages()
