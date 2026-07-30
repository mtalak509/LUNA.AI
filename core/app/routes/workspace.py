"""Session workspace HTTP routes.

User files (upload to any path inside the zone, download, delete, flat listing for the UI
picker) and the dialogue history materialized in the workspace. The handlers stay thin: the
file operations live in `WorkspaceManager`, shared with the CLI `/attach`.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from fastapi.responses import FileResponse

from core.agent.messages import MessageStore
from core.agent.session import AgentSession
from core.agent.tools.fs_paths import FileAccessError
from core.agent.workspace import (
    MAX_UPLOAD_BYTES,
    FileConflictError,
    FileMissingError,
    FileTooLargeError,
    WorkspaceManager,
)

from ._deps import get_session, session_paths

router = APIRouter()

# --- Session history ---


@router.get("/sessions/{session_id}/history")
async def get_history(paths: tuple[Path, Path] = Depends(session_paths)) -> dict:
    """Получить историю сессии в формате `messages_to_dict` (`{type, data}`)."""
    workspace_path, _ = paths
    return MessageStore(workspace_path).load_ui_messages()


# --- Workspace files ---


def get_workspace(paths: tuple[Path, Path] = Depends(session_paths)) -> WorkspaceManager:
    """Manager for the session's zone. Depends on `session_paths`: registry first, path after."""
    workspace_path, checkpoints_root = paths
    return WorkspaceManager(workspace_path=workspace_path, checkpoints_root=checkpoints_root)


@contextmanager
def _as_http_error() -> Iterator[None]:
    """Translate the manager's error taxonomy into status codes."""
    try:
        yield
    except FileAccessError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except FileConflictError as e:
        raise HTTPException(status_code=409, detail=f"{e} (use ?overwrite=true)") from e
    except FileTooLargeError as e:
        raise HTTPException(status_code=413, detail=str(e)) from e
    except FileMissingError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e


def _require_idle(session: AgentSession) -> None:
    """Mutations wait for the agent — a write mid-turn races the turn's own writes.

    Reads are deliberately not gated: the UI picker keeps working while the agent thinks.
    """
    if session.is_busy:
        raise HTTPException(status_code=409, detail="Session is busy")


@router.post("/sessions/{session_id}/workspace/files", status_code=201)
async def upload_file(
    file: UploadFile,
    path: str | None = None,
    overwrite: bool = False,
    session: AgentSession = Depends(get_session),
    workspace: WorkspaceManager = Depends(get_workspace),
) -> dict:
    """Upload a file to an arbitrary path inside the workspace zone."""
    _require_idle(session)

    # read(cap + 1): never buffer beyond the cap — an oversized body is detected by length.
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    with _as_http_error():
        saved = workspace.save(path or file.filename or "", content, overwrite=overwrite)
    return {"path": saved.path, "size": saved.size, "checkpoint": saved.checkpoint}


@router.get("/sessions/{session_id}/workspace/files")
async def list_workspace_files(workspace: WorkspaceManager = Depends(get_workspace)) -> dict:
    """Flat listing of the zone — feeds the UI picker; never goes into the agent context."""
    return {
        "files": [
            {"path": f.path, "size": f.size, "mtime": f.mtime.isoformat()} for f in workspace.list()
        ]
    }


@router.get("/sessions/{session_id}/workspace/files/{path:path}")
async def get_workspace_file(
    path: str,
    workspace: WorkspaceManager = Depends(get_workspace),
) -> FileResponse:
    """Download a file from the workspace."""
    with _as_http_error():
        target = workspace.open_for_read(path)
    return FileResponse(path=target, filename=target.name)


@router.delete("/sessions/{session_id}/workspace/files/{path:path}")
async def delete_workspace_file(
    path: str,
    session: AgentSession = Depends(get_session),
    workspace: WorkspaceManager = Depends(get_workspace),
) -> dict:
    """Delete a file from the workspace."""
    _require_idle(session)
    with _as_http_error():
        checkpoint = workspace.delete(path)
    return {"deleted": path, "checkpoint": checkpoint}
