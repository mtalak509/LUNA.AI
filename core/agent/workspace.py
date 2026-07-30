"""WorkspaceManager — file operations on one session's workspace zone, on the user's behalf.

Two layers, one class. The **zone layer** works anywhere inside `workspace/`: write a file,
read one back, delete one, list what is there. The **attachments layer** is a narrow facet of
the same operations pinned to one directory, `workspace/attachments/`, and it is what the user
interfaces expose for uploads.

Why attachments get their own directory: the agent needs to know which files the user gave it,
and the directory *is* that knowledge. Provenance comes from the location, so a listing of that
one directory at the moment of the model call is the whole story — no journal of past uploads
that could disagree with the filesystem, and nothing to go stale when a file is deleted.

The seam is shared by the HTTP routes (`core/app/routes/workspace.py`) and the CLI `/attach`.
The transport differs on purpose — the web goes over HTTP, the CLI works on the filesystem
in-process — but everything below it must not: the same path policy (`PathScope`, the one the
agent's file tools obey), the same size cap, the same checkpoint. A divergence here means undo
scenarios tried in the REPL say nothing about the server.

The checkpoint is taken by hand because these writes bypass `@with_checkpoint`, which only
wraps the agent's write tools. Without a snapshot the next `/undo` rolls the zone back to the
state before the write and silently swallows the user's file.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

from core.agent.checkpoint import CheckpointManager
from core.agent.tools.fs_paths import FileAccessError, PathScope

_logger = logging.getLogger(__name__)

# Upload cap. Bodies are never buffered beyond it — see the read(cap + 1) trick in the route.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# The one directory the user's own files go into, relative to the zone root.
ATTACHMENTS_DIRNAME = "attachments"

_RUNTIME_DIRNAME = ".runtime"


class WorkspaceError(Exception):
    """Base for workspace operation failures. Path violations stay `FileAccessError`."""


class FileConflictError(WorkspaceError):
    """Target already exists and overwrite was not requested."""


class FileTooLargeError(WorkspaceError):
    """Content exceeds `MAX_UPLOAD_BYTES`."""


class FileMissingError(WorkspaceError):
    """No such file."""


@dataclass(frozen=True)
class SavedFile:
    """A write: path (relative to the root the call worked in), byte size, checkpoint id."""

    path: str
    size: int
    checkpoint: str | None


@dataclass(frozen=True)
class ListedFile:
    path: str
    size: int
    mtime: datetime


def list_attachments(workspace_path: Path) -> list[ListedFile]:
    """Flat listing of `attachments/`, sorted by path. Missing directory → empty list.

    A plain function, not only a method: the context provider needs this listing and nothing
    else, and should not have to satisfy the write-side dependencies of the manager.
    """
    return _list_tree(Path(workspace_path) / ATTACHMENTS_DIRNAME)


def _list_tree(root: Path) -> list[ListedFile]:
    """Every file under `root`, `.runtime/` excluded, paths relative to `root`."""
    if not root.is_dir():
        return []
    out: list[ListedFile] = []
    for entry in root.rglob("*"):
        inside = entry.relative_to(root)
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


class WorkspaceManager:
    """The user's file operations on one session's zone, plus the attachments facet.

    Holds no state: every call re-derives paths from the roots it was constructed with.
    """

    def __init__(self, *, workspace_path: Path, checkpoints_root: Path) -> None:
        self.workspace_path = Path(workspace_path).resolve()
        self.checkpoints_root = Path(checkpoints_root)

    # --- the zone: paths relative to workspace/ ---------------------------------------

    def save(self, rel: str, content: bytes, *, overwrite: bool = False) -> SavedFile:
        """Write `content` to `rel` inside the zone, creating parent directories."""
        return self._write(self.resolve(rel), content, overwrite=overwrite, verb="write")

    def delete(self, rel: str) -> str | None:
        """Delete a file from the zone. Returns the checkpoint id."""
        return self._unlink(self.resolve(rel), verb="delete")

    def open_for_read(self, rel: str) -> Path:
        """Validated absolute path of an existing file — for streaming it to the client."""
        return self._existing_file(self.resolve(rel))

    def list(self) -> list[ListedFile]:
        """Flat listing of the whole zone, `.runtime/` excluded."""
        return _list_tree(self.workspace_path)

    def resolve(self, rel: str) -> Path:
        """Client-supplied relative path → absolute path inside the zone.

        The access policy is `PathScope`'s (no absolute paths, no `..` escape, no
        `.runtime/`), so HTTP, CLI and the agent's tools all enforce the same rules. Three
        narrowings on top:

        - an absolute path is refused here rather than deeper down, because callers may
          prefix the path (see `resolve_attachment`) and prefixing flattens `/etc/passwd`
          into an innocent-looking relative path, losing the very absoluteness `PathScope`
          looks for;
        - a drive letter or UNC root is refused too. `PathScope` reads paths as posix, where
          `C:/x` is *relative*, and `pathlib` then joins `C:` with the zone's own drive — so
          the path would quietly land at the zone root instead of being refused;
        - dotfiles are refused: a user file is not runtime infrastructure.
        """
        normalized = self._normalize(rel)
        scope = PathScope(agent_scope="main", workspace_path=self.workspace_path)
        return self._confine(scope.resolve_write(normalized), self.workspace_path, rel)

    # --- attachments: the same operations pinned to attachments/ -----------------------

    @property
    def attachments_root(self) -> Path:
        return self.workspace_path / ATTACHMENTS_DIRNAME

    def attach(self, rel: str, content: bytes, *, overwrite: bool = False) -> SavedFile:
        """Write `content` as an attachment. `rel` is relative to `attachments/`."""
        saved = self._write(
            self.resolve_attachment(rel), content, overwrite=overwrite, verb="attach"
        )
        return replace(saved, path=self._strip_attachments(saved.path))

    def attach_from_host(
        self, source: Path, rel: str | None = None, *, overwrite: bool = False
    ) -> SavedFile:
        """Copy a file from the host filesystem into `attachments/` (the CLI `/attach`).

        `rel` defaults to the source file name.
        """
        source = Path(source).expanduser()
        if not source.is_file():
            raise FileMissingError(f"No such file: {source}")
        # Checked against `stat`, so an oversized file is never read into memory.
        if source.stat().st_size > MAX_UPLOAD_BYTES:
            raise FileTooLargeError(f"File exceeds {MAX_UPLOAD_BYTES} bytes")
        return self.attach(rel or source.name, source.read_bytes(), overwrite=overwrite)

    def delete_attachment(self, rel: str) -> str | None:
        return self._unlink(self.resolve_attachment(rel), verb="detach")

    def open_attachment(self, rel: str) -> Path:
        return self._existing_file(self.resolve_attachment(rel))

    def list_attachments(self) -> list[ListedFile]:
        return list_attachments(self.workspace_path)

    def resolve_attachment(self, rel: str) -> Path:
        """Path relative to `attachments/` → absolute path, confined to that directory.

        Same policy as `resolve`, one narrowing more: the result may not leave
        `attachments/`. Everything the user uploads lands there and nowhere else, which is
        what lets the agent's context block be a plain listing of one directory.
        """
        normalized = self._normalize(rel)
        target = self.resolve(f"{ATTACHMENTS_DIRNAME}/{normalized}")
        return self._confine(target, self.attachments_root, rel)

    # --- shared internals -------------------------------------------------------------

    def _normalize(self, rel: str) -> str:
        normalized = rel.replace("\\", "/").strip()
        name = PurePosixPath(normalized).name if normalized else ""
        if not name or name.startswith("."):
            raise FileAccessError(f"Invalid file name: {rel!r}")
        if PurePosixPath(normalized).is_absolute() or PureWindowsPath(normalized).drive:
            raise FileAccessError(f"Absolute path is not allowed: {rel!r}")
        return normalized

    def _confine(self, target: Path, root: Path, rel: str) -> Path:
        """Refuse a resolved path that escaped `root`.

        For the zone this catches the `subagents/` prefix `PathScope` grants the main agent
        for promotion; for attachments, any way out of that directory.
        """
        try:
            target.relative_to(root)
        except ValueError:
            raise FileAccessError(f"Path outside {root.name}/: {rel!r}") from None
        return target

    def _write(self, target: Path, content: bytes, *, overwrite: bool, verb: str) -> SavedFile:
        if len(content) > MAX_UPLOAD_BYTES:
            raise FileTooLargeError(f"File exceeds {MAX_UPLOAD_BYTES} bytes")
        if target.exists() and not overwrite:
            raise FileConflictError(f"File already exists: {self._relative(target)!r}")

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        checkpoint = self._snapshot(f"{verb} {target.name}")
        rel = self._relative(target)
        _logger.info("%s %s (%d bytes, checkpoint %s)", verb, rel, len(content), checkpoint)
        return SavedFile(path=rel, size=len(content), checkpoint=checkpoint)

    def _unlink(self, target: Path, *, verb: str) -> str | None:
        self._existing_file(target).unlink()
        checkpoint = self._snapshot(f"{verb} {target.name}")
        _logger.info("%s %s (checkpoint %s)", verb, self._relative(target), checkpoint)
        return checkpoint

    def _existing_file(self, target: Path) -> Path:
        if not target.is_file():
            raise FileMissingError(f"File not found: {self._relative(target)!r}")
        return target

    def _relative(self, target: Path) -> str:
        return target.relative_to(self.workspace_path).as_posix()

    def _strip_attachments(self, rel: str) -> str:
        """Zone-relative path → attachments-relative: what the attachments API speaks."""
        prefix = f"{ATTACHMENTS_DIRNAME}/"
        return rel[len(prefix) :] if rel.startswith(prefix) else rel

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
