"""
PathScope — резолвер относительного пути файлового тула в безопасный абсолютный путь
внутри разрешённой зоны агента.

Несущий шов блока: все шесть файловых тулов (read/write/edit/list/search/delete) ходят
через него, и именно здесь физически живёт изоляция агентов  —
граница между MainAgent и субагентом в V2 не процессная, а на уровне фильтрации путей.

Политика доступа (v2-storage §3.6/§6):
    MainAgent  : RW /session/workspace/ (кроме .runtime/)
                + RW /session/subagents/<uid>/ (по явному префиксу `subagents/`) —
                  для чтения результата субагента (промоут) и cleanup
    Субагент   : RW своя зона /session/subagents/<uid>/ (кроме своего .runtime/)
                + RO-вид /session/workspace/ (по явному префиксу `workspace/`)
    .runtime/  : недоступен файловым тулам в ЛЮБОЙ зоне, и на чтение, и на запись.

Префиксы `workspace/` (у субагента) и `subagents/` (у MainAgent) симметричны: каждый —
явный адрес соседней зоны на томе сессии (`/session/workspace` и `/session/subagents` —
соседи). Резолв соседней зоны идёт через `_safe_join` под её корень, поэтому `..`-побег
обратно (напр. `subagents/../workspace/.runtime`) блокируется как выход за пределы корня.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Protocol

from langchain_core.tools import ToolException

# Имя поддиректории для runtime-инфраструктуры. Заблокирована для любого тула.
_RUNTIME_DIR = ".runtime"
# Full output of a truncated tool result. Readable by `read`, but not a working file.
SPILL_DIR = ".truncated_outputs"
# Service directories that listings and searches never show.
HIDDEN_DIRS = frozenset({_RUNTIME_DIR, SPILL_DIR})
# Явный префикс, которым субагент адресует RO-вид общего workspace из своей зоны.
_WORKSPACE_PREFIX = "workspace"
# Явный префикс, которым MainAgent адресует зоны субагентов (чтение результата / cleanup).
_SUBAGENTS_PREFIX = "subagents"


class FileAccessError(ToolException):
    """Domain access error of a file tool.

    It reaches the agent as a tool error (text like «доступ запрещён: …») instead of crashing
    the process, so the agent sees the refusal and corrects its next step. As a ToolException
    it is a regular tool outcome (a guardrail refusal), not a bug: ToolErrorMiddleware logs it
    without a traceback.
    """


def _safe_join(root: Path, rel: str) -> Path:
    """Safely resolve the relative `rel` under `root`. Any violation → FileAccessError.

    Guarantees, in order:
    1. `rel` is relative and not empty (absolute or empty is refused);
    2. the result does not leave `root` (`..` traversal or a symlink escape), checked AFTER
       normalization, with `relative_to`;
    3. `.runtime/` cannot be addressed (also checked after normalization, otherwise
       `foo/../.runtime` would slip through).
    """
    if not rel or not rel.strip():
        raise FileAccessError("Пустой путь")

    pure = PurePosixPath(rel)
    if pure.is_absolute():
        raise FileAccessError(f"Абсолютный путь {rel!r} запрещён")

    root_resolved = root.resolve()
    # joinpath(*parts) + resolve(): '..' и симлинки схлопываются до реального пути.
    target = root_resolved.joinpath(*pure.parts).resolve()

    # Побег за пределы root:
    try:
        inside = target.relative_to(root_resolved)
    except ValueError:
        raise FileAccessError(f"Путь вне зоны агента: {rel!r}") from None

    # .runtime/ — недоступен:
    if inside.parts and inside.parts[0] == _RUNTIME_DIR:
        raise FileAccessError(f"Доступ к {_RUNTIME_DIR} запрещён")

    return target


class ZoneContext(Protocol):
    """The part of the agent runtime context that defines its file zones."""

    agent_scope: str
    workspace_path: Path
    subagent_path: Path | None


class PathScope:
    """Резолвер путей под конкретного агента. Собирается на вызов тула из рантайм-контекста
    (`agent_scope`, `workspace_path`, путь своей зоны субагента).
    """

    def __init__(
        self,
        *,
        agent_scope: str,
        workspace_path: Path,
        subagent_path: Path | None = None,
    ) -> None:
        if agent_scope not in ("main", "subagent"):
            raise ValueError(f"Неизвестный агент: {agent_scope!r}")
        if agent_scope == "subagent" and subagent_path is None:
            raise ValueError("subagent-скоупу необходим subagent_path")

        self.agent_scope = agent_scope
        self.workspace_path = Path(workspace_path)
        self.subagent_path = Path(subagent_path) if subagent_path else None

    @classmethod
    def from_context(cls, context: ZoneContext) -> PathScope:
        """Scope of the agent whose runtime context (`get_runtime().context`) is given."""
        return cls(
            agent_scope=context.agent_scope,
            workspace_path=context.workspace_path,
            subagent_path=context.subagent_path,
        )

    @property
    def spill_dir(self) -> Path:
        """Where truncated tool output is saved: inside the agent's own RW zone.

        The one place that decides the location, so it can move together with session state.
        """
        return self._rw_root / SPILL_DIR

    @property
    def _rw_root(self) -> Path:
        """RW-зона агента: workspace у main, своя зона у субагента."""
        return self.workspace_path if self.agent_scope == "main" else self.subagent_path  # type: ignore[return-value]

    @property
    def _subagents_root(self) -> Path:
        """Корень зон субагентов: /session/subagents — сосед /session/workspace.

        Фиксированная раскладка тома сессии (v2-storage §3.1/§3.6): workspace и subagents —
        соседи под /session/. Выводим из workspace_path.parent, не требуя отдельного поля.
        """
        return self.workspace_path.parent / _SUBAGENTS_PREFIX

    def resolve_write(self, rel: str) -> Path:
        """Path to write to: the agent's RW zone, plus subagent zones for the MainAgent (cleanup).

        Spilled output is read-only: a file written there would be hidden from ls/find/grep.
        """
        if self.agent_scope == "subagent" and _has_workspace_prefix(rel):
            raise FileAccessError(f"workspace/ доступен субагенту только на чтение: {rel!r}")
        if self.agent_scope == "main" and _has_subagents_prefix(rel):
            root, sub = self._subagents_root, _strip_first_segment(rel)
        else:
            root, sub = self._rw_root, rel
        target = _safe_join(root, sub)
        if SPILL_DIR in target.relative_to(root.resolve()).parts:
            raise FileAccessError(f"{SPILL_DIR}/ доступна только на чтение: {rel!r}")
        return target

    def resolve_read(self, rel: str) -> Path:
        """Путь под чтение — RW-зона агента; плюс соседняя зона по явному префиксу:
        субагенту — RO-вид workspace (`workspace/`), MainAgent — зоны субагентов (`subagents/`)."""
        if self.agent_scope == "subagent" and _has_workspace_prefix(rel):
            return _safe_join(self.workspace_path, _strip_first_segment(rel))
        if self.agent_scope == "main" and _has_subagents_prefix(rel):
            return _safe_join(self._subagents_root, _strip_first_segment(rel))
        return _safe_join(self._rw_root, rel)


def _has_workspace_prefix(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return bool(parts) and parts[0] == _WORKSPACE_PREFIX


def _has_subagents_prefix(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return bool(parts) and parts[0] == _SUBAGENTS_PREFIX


def _strip_first_segment(rel: str) -> str:
    """Убрать ведущий сегмент-префикс (`workspace`/`subagents`), вернуть остаток пути."""
    parts = PurePosixPath(rel).parts[1:]
    return str(PurePosixPath(*parts)) if parts else "."
