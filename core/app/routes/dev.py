"""
Эндпоинты file-viewer'а / чекпоинт-панели (fs/tree, fs/file, checkpoints, restore).

Исторически «dev-слой» за флагом SERVER_DEV_ENDPOINTS; с промоутом devfront в
продуктовый UI флаг упразднён — роутер регистрируется всегда.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, Depends, HTTPException, Request

from core.agent.checkpoint import CheckpointManager, _DIR_RE
from core.agent.session import AgentSession

from ._deps import get_session, session_paths

router = APIRouter()

_MAX_DEV_FILE_BYTES = 1_048_576  # 1 MiB — лимит чтения файла через dev-эндпоинт
_SKIP_FS_TREE_DIRS = frozenset({"checkpoints"})


def _validate_checkpoint_id(cp_id: str) -> None:
    """Отвергнуть cp_id, который не является именем снимка (`cNNN_label`).

    URL-`cp_id` уходит в арифметику пути (`checkpoints_root / cp_id`), поэтому валидируем
    как имя каталога: строгий шаблон `_DIR_RE` + запрет разделителей/`..` (защита от escape
    вида `../../outside`, который иначе резолвится вне checkpoints_root — CR-02).
    """
    if (
        not cp_id
        or _DIR_RE.match(cp_id) is None
        or ".." in cp_id
        or "/" in cp_id
        or "\\" in cp_id
    ):
        raise HTTPException(status_code=400, detail="Invalid checkpoint id")


def _resolve_session_path(session_root: Path, session_id: str, rel_path: str) -> Path:
    """Безопасный путь под session_root/{session_id}/; .runtime/ разрешён.
    Звать ТОЛЬКО после get_session — id к этому моменту проверен по реестру."""
    if not rel_path or not rel_path.strip():
        raise HTTPException(status_code=400, detail="Empty path")

    pure = PurePosixPath(rel_path.replace("\\", "/"))
    if pure.is_absolute():
        raise HTTPException(status_code=400, detail="Absolute path forbidden")

    session_dir = (session_root / session_id).resolve()
    target = session_dir.joinpath(*pure.parts).resolve()
    try:
        target.relative_to(session_dir)
    except ValueError:
        raise HTTPException(status_code=403, detail="Path outside session") from None
    return target


def _sort_dir_entries(entries: list[Path]) -> list[Path]:
    return sorted(entries, key=lambda p: (not p.is_dir(), p.name.lower()))


def _fs_node(entry: Path, rel_path: str) -> dict:
    stat = entry.stat()
    mtime = datetime.fromtimestamp(stat.st_mtime).isoformat()
    if entry.is_dir():
        children = [
            _fs_node(child, f"{rel_path}/{child.name}")
            for child in _sort_dir_entries(list(entry.iterdir()))
        ]
        return {
            "name": entry.name,
            "path": rel_path,
            "type": "dir",
            "mtime": mtime,
            "children": children,
        }
    return {
        "name": entry.name,
        "path": rel_path,
        "type": "file",
        "size": stat.st_size,
        "mtime": mtime,
    }


def _build_session_fs_tree(session_dir: Path) -> list[dict]:
    """Дерево workspace/ + subagents/; checkpoints/ не включается."""
    if not session_dir.is_dir():
        return []
    nodes: list[dict] = []
    for child in _sort_dir_entries(
        [p for p in session_dir.iterdir() if p.is_dir() and p.name not in _SKIP_FS_TREE_DIRS]
    ):
        nodes.append(_fs_node(child, child.name))
    return nodes


@router.get("/sessions/{session_id}/fs/tree")
async def fs_tree(
    session_id: str,
    request: Request,
    _session: AgentSession = Depends(get_session),
) -> list[dict]:
    """Рекурсивное дерево ФС сессии (workspace + subagents)."""
    session_dir = request.app.state.session_root / session_id
    return _build_session_fs_tree(session_dir)


@router.get("/sessions/{session_id}/fs/file")
async def fs_file(
    session_id: str,
    path: str,
    request: Request,
    _session: AgentSession = Depends(get_session),
) -> dict:
    """Прочитать текстовый файл сессии (read-only, включая .runtime/)."""
    target = _resolve_session_path(request.app.state.session_root, session_id, path)
    if not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")

    size = target.stat().st_size
    if size > _MAX_DEV_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File too large")

    raw = target.read_bytes()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=415, detail="Binary or non-UTF-8 file") from None

    stat = target.stat()
    return {
        "path": path.replace("\\", "/"),
        "content": content,
        "size": size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(),
    }


@router.get("/sessions/{session_id}/checkpoints")
async def list_checkpoints(paths: tuple[Path, Path] = Depends(session_paths)) -> list[dict]:
    """Список снимков workspace; новейший первым."""
    workspace_path, checkpoints_root = paths
    manager = CheckpointManager(
        workspace_path=workspace_path,
        checkpoints_root=checkpoints_root,
    )
    return [
        {
            "id": cp.id,
            "label": cp.label,
            "mtime": cp.created_at.isoformat(),
        }
        for cp in reversed(manager.list())
    ]


@router.post("/sessions/{session_id}/checkpoints/{cp_id}/restore")
async def restore_checkpoint(
    cp_id: str,
    session: AgentSession = Depends(get_session),
    paths: tuple[Path, Path] = Depends(session_paths),
) -> dict:
    """Откат workspace к снимку cp_id."""
    if session.is_busy:
        raise HTTPException(status_code=409, detail="Session is busy")

    _validate_checkpoint_id(cp_id)
    workspace_path, checkpoints_root = paths

    # Belt-and-suspenders: даже после валидации имени убеждаемся, что резолв не увёл
    # путь за пределы checkpoints_root, прежде чем менеджер тронет ФС (CR-02).
    checkpoints_root = checkpoints_root.resolve()
    src = (checkpoints_root / cp_id).resolve()
    if src.parent != checkpoints_root or not src.is_dir():
        raise HTTPException(status_code=404, detail="Unknown checkpoint")

    manager = CheckpointManager(
        workspace_path=workspace_path,
        checkpoints_root=checkpoints_root,
    )
    try:
        manager.restore(cp_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e

    # Откат заменил .runtime/messages.jsonl на диске — ре-гидратируем живую историю
    # агента, иначе он продолжит ход со stale in-memory сообщениями (HI-01). Безопасно:
    # сессия не busy (проверено выше), конкурентного хода нет.
    session._agent.reload_history()
    return {"restored": cp_id}
