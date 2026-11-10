"""Direct user controls for session-owned shell tasks."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from core.agent.session import AgentSession
from core.agent.shell.models import ShellError, ShellTaskView
from core.agent.shell.shell_manager import ShellManager

from ._deps import get_session

router = APIRouter(prefix="/sessions/{session_id}/shell")


def get_shell_manager(session: Annotated[AgentSession, Depends(get_session)]) -> ShellManager:
    manager = session.shell_manager
    if manager is None:
        raise HTTPException(status_code=503, detail="Shell manager unavailable")
    return manager


@router.get("/tasks")
async def list_tasks(
    manager: Annotated[ShellManager, Depends(get_shell_manager)],
) -> list[ShellTaskView]:
    return manager.list_tasks()


@router.post("/tasks/{task_id}/stop")
async def stop_task(
    task_id: str,
    manager: Annotated[ShellManager, Depends(get_shell_manager)],
) -> ShellTaskView:
    try:
        manager.status(task_id)
    except ShellError as exc:
        raise HTTPException(status_code=404, detail="Unknown shell task") from exc
    return await manager.stop(task_id)
