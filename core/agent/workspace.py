"""WorkspaceManager — user file operations over one session's workspace zone.

The seam shared by the HTTP routes (`core/app/routes/workspace.py`) and the CLI `/attach`
command. The transport differs on purpose — the web goes over HTTP, the CLI works on the
filesystem in-process — but everything below it must not: the same path policy (`PathScope`,
the one the agent's file tools obey), the same size cap, the same checkpoint. A divergence
here means undo scenarios tried in the REPL say nothing about the server.

The checkpoint is taken by hand because these writes bypass `@with_checkpoint`, which only
wraps the agent's write tools. Without a snapshot the next `/undo` rolls the zone back to
the state before the upload and silently swallows the user's file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from core.agent.checkpoint import CheckpointManager
from core.agent.tools.fs_paths import FileAccessError, PathScope

_logger = logging.getLogger(__name__)

# Upload cap. Bodies are never buffered beyond it — see the read(cap + 1) trick in the route.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

_RUNTIME_DIRNAME = ".runtime"


class WorkspaceError(Exception):
    """Base for workspace operation failures. Path violations stay `FileAccessError`."""


class FileConflictError(WorkspaceError):
    """Target already exists and overwrite was not requested."""


class FileTooLargeError(WorkspaceError):
    """Content exceeds `MAX_UPLOAD_BYTES`."""


class FileMissingError(WorkspaceError):
    """No such file in the zone."""


@dataclass(frozen=True)
class SavedFile:
    """Result of a write: zone-relative path, byte size, checkpoint id (None if it failed)."""

    path: str
    size: int
    checkpoint: str | None


@dataclass(frozen=True)
class ListedFile:
    path: str
    size: int
    mtime: datetime


class WorkspaceManager:
    """User files inside one session's workspace zone.

    Holds no state: every call re-derives paths from the roots it was constructed with.
    """

    def __init__(self, *, workspace_path: Path, checkpoints_root: Path) -> None:
        self.workspace_path = Path(workspace_path).resolve()
        self.checkpoints_root = Path(checkpoints_root)

    # --- writes ----------------------------------------------------------------------

    def save(self, rel: str, content: bytes, *, overwrite: bool = False) -> SavedFile:
        """Write `content` to `rel` inside the zone, creating parent directories."""
        if len(content) > MAX_UPLOAD_BYTES:
            raise FileTooLargeError(f"File exceeds {MAX_UPLOAD_BYTES} bytes")

        target = self.resolve(rel)
        if target.exists() and not overwrite:
            raise FileConflictError(f"File already exists: {rel!r}")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        checkpoint = self._snapshot(f"upload {target.name}")
        _logger.info("File uploaded: %s (%d bytes, checkpoint %s)", rel, len(content), checkpoint)
        return SavedFile(path=self._relative(target), size=len(content), checkpoint=checkpoint)

    def copy_from_host(
        self, source: Path, rel: str | None = None, *, overwrite: bool = False
    ) -> SavedFile:
        """Copy a file from the host filesystem into the zone (the CLI `/attach`).

        `rel` defaults to the source file name, i.e. the root of the zone.
        """
        source = Path(source).expanduser()
        if not source.is_file():
            raise FileMissingError(f"No such file: {source}")
        if source.stat().st_size > MAX_UPLOAD_BYTES:
            raise FileTooLargeError(f"File exceeds {MAX_UPLOAD_BYTES} bytes")
        return self.save(rel or source.name, source.read_bytes(), overwrite=overwrite)

    def delete(self, rel: str) -> str | None:
        """Delete a file from the zone. Returns the checkpoint id."""
        target = self.resolve(rel)
        if not target.is_file():
            raise FileMissingError(f"File not found: {rel!r}")

        target.unlink()
        checkpoint = self._snapshot(f"delete {target.name}")
        _logger.info("File deleted: %s (checkpoint %s)", rel, checkpoint)
        return checkpoint

    # --- reads -----------------------------------------------------------------------

    def open_for_read(self, rel: str) -> Path:
        """Validated absolute path of an existing file — for streaming it to the client."""
        target = self.resolve(rel)
        if not target.is_file():
            raise FileMissingError(f"File not found: {rel!r}")
        return target

    def list(self) -> list[ListedFile]:
        """Flat listing of the zone, `.runtime/` excluded, sorted by path."""
        if not self.workspace_path.is_dir():
            return []
        out: list[ListedFile] = []
        for entry in self.workspace_path.rglob("*"):
            inside = entry.relative_to(self.workspace_path)
            if not entry.is_file() or _RUNTIME_DIRNAME in inside.parts:
                continue
            stat = entry.stat()
            out.append(
                ListedFile(
                    path=inside.as_posix(),
                    size=stat.st_size,
                    mtime=datetime.fromtimestamp(stat.st_mtime, tz=UTC),
                )
            )
        return sorted(out, key=lambda f: f.path)

    # --- paths -----------------------------------------------------------------------

    def resolve(self, rel: str) -> Path:
        """Client-supplied relative path → absolute path inside the zone.

        The access policy is `PathScope`'s (no absolute paths, no `..` escape, no
        `.runtime/`), so HTTP, CLI and the agent's tools all enforce the same rules. Three
        narrowings on top:

        - a drive letter or UNC root is rejected outright. `PathScope` reads paths as posix,
          where `C:/x` is *relative*, and `pathlib` then joins `C:` with the zone's own drive
          — so the path would quietly land at the zone root instead of being refused;
        - dotfiles are rejected: a user file is not runtime infrastructure;
        - the file must land in the workspace itself, never in a subagent zone — that one
          `PathScope` grants the main agent for promotion, not for uploads.
        """
        normalized = rel.replace("\\", "/").strip()
        name = PurePosixPath(normalized).name if normalized else ""
        if not name or name.startswith("."):
            raise FileAccessError(f"Invalid file name: {rel!r}")
        if PureWindowsPath(normalized).drive:
            raise FileAccessError(f"Absolute path is not allowed: {rel!r}")

        scope = PathScope(agent_scope="main", workspace_path=self.workspace_path)
        target = scope.resolve_write(normalized)
        try:
            target.relative_to(self.workspace_path)
        except ValueError:
            raise FileAccessError(f"Path outside the workspace: {rel!r}") from None
        return target

    def _relative(self, target: Path) -> str:
        return target.relative_to(self.workspace_path).as_posix()

    def _snapshot(self, label: str) -> str | None:
        """Snapshot the zone after a write. A failed snapshot must not fail a done write.

        Same trade-off as `checkpoint._try_snapshot`: the file is already on disk, so
        reporting failure would invite a retry of a non-idempotent operation.
        """
        try:
            manager = CheckpointManager(
                workspace_path=self.workspace_path,
                checkpoints_root=self.checkpoints_root,
            )
            return manager.snapshot(label=label).name
        except Exception:
            _logger.warning("checkpoint snapshot failed for label=%r", label, exc_info=True)
            return None
