"""
CheckpointManager + декоратор `@with_checkpoint` — снимок/откат рабочего тома.

Материализация факта Ф3: чекпоинт — это копия каталога /session/workspace/, а НЕ запись
checkpointer'а LangGraph (его в V2 нет). Источник истины — ФС; снимок тома и есть точка
восстановления.

Правила (v2-storage §5):
- снимок берёт /session/workspace/ ЦЕЛИКОМ, включая .runtime/ (иначе после отката
  messages.jsonl окажется «в будущем» относительно файлов документа);
- зоны субагентов /session/subagents/<uid>/ в снимок НЕ входят (они вне workspace);
- хранилище /checkpoints/ лежит ВНЕ /session/, чтобы snapshot не копировал сам себя;
- семантика «после операции»: cN фиксирует состояние ПОСЛЕ успешного write-тула;
- откат до cK = полная замена workspace содержимым cK (не merge).
"""

from __future__ import annotations

import functools
import logging
import re
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from langgraph.runtime import get_runtime

_logger = logging.getLogger(__name__)

# Имя каталога снимка: c<NNN>_<label>. NNN — монотонный номер, label — slug описания.
_DIR_RE = re.compile(r"^c(\d+)_(.*)$")


@dataclass(frozen=True)
class CheckpointInfo:
    id: str              # имя каталога снимка, напр. "c003_после_правки_маршрута"
    n: int               # монотонный номер
    label: str           # человекочитаемое описание (slug)
    created_at: datetime  # время создания (mtime каталога)


def _slugify(label: str) -> str:
    r"""Описание → безопасный для имени каталога фрагмент. \w покрывает кириллицу."""
    cleaned = re.sub(r"\s+", "_", label.strip())
    cleaned = re.sub(r"[^\w\-]", "", cleaned)
    return cleaned or "snapshot"


def _parse_dirname(name: str) -> tuple[int, str] | None:
    """'c003_label' → (3, 'label'); не-снимок → None."""
    m = _DIR_RE.match(name)
    if not m:
        return None
    return int(m.group(1)), m.group(2)


class CheckpointManager:
    """Снимок/откат тома workspace. Состояния в памяти не держит — номер выводится из
    содержимого /checkpoints/ (П3).
    """

    def __init__(self, *, workspace_path: Path, checkpoints_root: Path) -> None:
        ws = Path(workspace_path).resolve()
        cp = Path(checkpoints_root).resolve()
        # /checkpoints/ обязан лежать вне workspace, иначе snapshot уйдёт в рекурсию.
        if cp == ws or ws in cp.parents:
            raise ValueError("checkpoints_root должен лежать вне workspace")
        self.workspace_path = ws
        self.checkpoints_root = cp

    # --- запись ----------------------------------------------------------------------

    def snapshot(self, label: str) -> Path:
        """Скопировать workspace/ в /checkpoints/c<N>_<label>/. Вернуть путь снимка."""
        self.checkpoints_root.mkdir(parents=True, exist_ok=True)
        n = self._next_n()
        dest = self.checkpoints_root / f"c{n:03d}_{_slugify(label)}"
        # workspace_path не содержит subagents/ (та зона — сосед, вне workspace), .runtime/
        # копируется штатно как часть дерева.
        shutil.copytree(self.workspace_path, dest)
        return dest

    # --- откат -----------------------------------------------------------------------

    def restore(self, checkpoint_id: str) -> None:
        """Полностью заменить workspace/ содержимым снимка checkpoint_id.

        НЕ создаёт новый чекпоинт и НЕ удаляет более поздние снимки (forward-снимки
        остаются — redo-политика вне MVP). Ре-гидратацию истории MainAgent из
        восстановленного .runtime/messages.jsonl делает вызывающий (оркестратор после
        /undo → MessageStore.load()) — менеджер трогает только ФС.
        """
        src = self.checkpoints_root / checkpoint_id
        if not src.is_dir():
            raise ValueError(f"неизвестный чекпоинт: {checkpoint_id!r}")
        if self.workspace_path.exists():
            shutil.rmtree(self.workspace_path)
        shutil.copytree(src, self.workspace_path)

    # --- чтение ----------------------------------------------------------------------

    def list(self) -> list[CheckpointInfo]:
        """Все снимки, отсортированные по номеру."""
        if not self.checkpoints_root.is_dir():
            return []
        out: list[CheckpointInfo] = []
        for entry in self.checkpoints_root.iterdir():
            if not entry.is_dir():
                continue
            parsed = _parse_dirname(entry.name)
            if parsed is None:
                continue
            n, label = parsed
            out.append(
                CheckpointInfo(
                    id=entry.name,
                    n=n,
                    label=label,
                    created_at=datetime.fromtimestamp(entry.stat().st_mtime),
                )
            )
        return sorted(out, key=lambda c: c.n)

    # --- внутреннее ------------------------------------------------------------------

    def _next_n(self) -> int:
        """Следующий монотонный номер = max(существующие) + 1, или 0 для пустого хранилища.

        Выводим из листинга /checkpoints/ (а НЕ из файла-счётчика в .runtime/): счётчик в
        .runtime/ попал бы в снимок и откатился вместе с workspace → коллизия номеров после
        restore. Листинг лежит вне /session/, не откатывается, монотонность сохраняется.
        """
        existing = [c.n for c in self.list()]
        return max(existing) + 1 if existing else 0


# --- декоратор `@with_checkpoint` ---------------------------------------------


def with_checkpoint(
    label: str | Callable[[dict[str, Any]], str] | None = None,
) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
    """Декоратор поверх write-тула: на УСПЕХ исполнения → снимок workspace.

    Порядок применения: `@agent_tool(...)` снаружи, `@with_checkpoint()` ближе к функции —
    снимок берётся внутри логики тула, после возврата результата.

    Семантика «после операции» (v2-storage §5.2): снимок ПОСЛЕ успешного тула. Исключение в
    туле (напр. FileAccessError) пробрасывается до строки снапшота → чекпоинта нет, как и
    требует §5.4 («failed tool call чекпоинт не создаёт»).

    `label`: строка | callable(kwargs)->str | None. None → имя функции тула.

    Менеджер не хранится — собирается на вызов из рантайм-контекста (`workspace_path` +
    `checkpoints_root` через `get_runtime()`), что согласовано со stateless-дизайном
    CheckpointManager и принципом П3 (никаких сессионных кэшей в памяти).
    """

    def decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            result = await func(*args, **kwargs)  # ошибка тула пробросится отсюда, до снимка
            _try_snapshot(_resolve_label(label, func, kwargs))
            return result

        return wrapper

    return decorator


def _resolve_label(
    label: str | Callable[[dict[str, Any]], str] | None,
    func: Callable[..., Any],
    kwargs: dict[str, Any],
) -> str:
    if callable(label):
        return label(kwargs)
    if isinstance(label, str):
        return label
    return func.__name__


def _try_snapshot(label: str) -> None:
    """Снять чекпоинт, не роняя тул при сбое снимка (write уже состоялся)."""
    ctx = get_runtime().context
    checkpoints_root = getattr(ctx, "checkpoints_root", None)
    if checkpoints_root is None:
        # Декоратор инертен, пока checkpoints_root не проброшен в контекст (проводка —
        # слой старта сессии / server.py). Это позволяет навешивать его на тулы заранее.
        _logger.debug("checkpoint skipped: checkpoints_root не сконфигурирован")
        return
    try:
        CheckpointManager(
            workspace_path=ctx.workspace_path,
            checkpoints_root=checkpoints_root,
        ).snapshot(label)
    except Exception:
        # Снимок — safety net. Если он упал, write уже выполнен: не маскируем успешный тул
        # ошибкой (иначе агент ретраит неидемпотентную операцию). Логируем и идём дальше.
        _logger.warning("checkpoint snapshot failed for label=%r", label, exc_info=True)
