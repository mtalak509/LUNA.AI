"""
`delegate_to_subagent` — единственный путь Main → Subagent.

Принцип П1: маршрут к субагенту возникает как tool call MainAgent, без супервайзора/графа-
оркестратора. Субагент — та же `BaseAgent` с холодной конфигурацией (П2), запускается как
`asyncio`-задача внутри процесса MainAgent (не отдельный ОС-процесс). Обработчик тула:
создаёт зону субагента, снимает чекпоинт ПЕРЕД работой, запускает субагента, мультиплексирует
его события в стрим MainAgent через `get_stream_writer()` и собирает контракт
`{summary, artifacts}` (subagent-output-contract: чистый advisor, никаких машинных патчей).

Краш субагента изолирован: исключение → штатный failed `tool_result`, петля MainAgent не падает.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.tools import ToolException
from langgraph.config import get_stream_writer
from langgraph.runtime import get_runtime

from core.agent.checkpoint import CheckpointManager
from core.agent.tools import agent_tool
from core.agent.tools._subagents import get_subagent_factory

_logger = logging.getLogger(__name__)

_ZONE_SUBDIRS = ("notes", "artifacts", ".runtime")
_ARTIFACTS_DIR = "artifacts"


class DelegationError(ToolException):
    """Ошибка делегирования (напр. неизвестный тип субагента) — отдаётся модели для
    self-correction. ToolErrorMiddleware превратит её в ToolMessage(status="error")."""


def _writer() -> Callable[[dict], Any] | None:
    """Стрим-writer custom-канала или None (вне графа get_stream_writer кинет RuntimeError).

    События субагента эмитятся best-effort: сбой стрима не должен ронять делегацию.
    """
    try:
        return get_stream_writer()
    except Exception:  # noqa: BLE001 — нет графа/писателя → событий просто не будет
        return None


def _emit(writer: Callable[[dict], Any] | None, payload: dict) -> None:
    """Эмитнуть событие в custom-канал MainAgent, не роняя обработчик при сбое стрима."""
    if writer is None:
        return
    try:
        writer(payload)
    except Exception:  # noqa: BLE001
        _logger.debug("subagent event emit skipped", exc_info=True)


def _snapshot_before(ctx: Any, label: str) -> None:
    """Снимок workspace ПЕРЕД делегацией (best-effort, как checkpoint._try_snapshot).

    НЕ `@with_checkpoint` (тот «после успеха»): делегации нужен снимок «до», чтобы `/undo`
    откатывал к состоянию ПЕРЕД работой субагента. Зона субагента в снимок не входит (она
    вне workspace). `checkpoints_root=None` → пропуск; сбой снимка → warning, не валит ход.
    """
    checkpoints_root = getattr(ctx, "checkpoints_root", None)
    if checkpoints_root is None:
        _logger.debug("checkpoint skipped: checkpoints_root не сконфигурирован")
        return
    try:
        CheckpointManager(
            workspace_path=ctx.workspace_path,
            checkpoints_root=checkpoints_root,
        ).snapshot(label)
    except Exception:  # noqa: BLE001 — снимок safety-net, не маскируем делегацию его сбоем
        _logger.warning("checkpoint before delegation failed: label=%r", label, exc_info=True)


def _list_artifacts(zone: Path, session_root: Path) -> list[str]:
    """Листинг файлов зоны субагента `artifacts/**` — ground truth контракта, НЕ self-report.

    Пути относительны от корня СВОЕЙ сессии (напр. `subagents/<uid>/artifacts/count.md`),
    чтобы MainAgent адресовал их своими тулами для промоута. Устраняет галлюцинированные пути.
    """
    artifacts_dir = zone / _ARTIFACTS_DIR
    if not artifacts_dir.is_dir():
        return []
    return sorted(
        str(p.relative_to(session_root).as_posix())
        for p in artifacts_dir.rglob("*")
        if p.is_file()
    )


@agent_tool(is_write=False, main_only=True)
async def delegate_to_subagent(
    subagent_type: str,
    task_text: str,
    task_id: str | None = None,
) -> dict:
    """Делегировать закрытую подзадачу субагенту-специалисту и получить его результат.

    Позволяет делегировать закрытую подзадачу субагенту-специалисту (доступные перечислены в твоём промпте) и получить его результат.
    Субагент всегда возвращает ссылки на артифакты и итог своей работы.

    Args:
        subagent_type: Тип специализации субагента.
        task_text: Самодостаточная постановка задачи.
        task_id: Опциональная привязка к пункту плана.

    Возвращает `{"summary": <итог субагента>, "artifacts": [<пути файлов>]}`.
    """
    factory = get_subagent_factory()
    ctx = get_runtime().context

    # Корень СВОЕЙ сессии — из контекста хода, не из фабрики (15.2): родитель workspace =
    # корень сессии — инвариант раскладки /session/<id>/{workspace,subagents,checkpoints}.
    session_root = ctx.workspace_path.parent
    subagents_root = session_root / "subagents"

    # 1. Неизвестный тип (нет agent.md) → self-correctable DelegationError (до зоны/чекпоинта).
    if not factory.resolve_agent_md(subagent_type).is_file():
        raise DelegationError(
            f"неизвестный тип субагента: {subagent_type!r}. "
            "Доступные типы перечислены в твоём промпте."
        )

    # 2. uid / namespace.
    uid = uuid4().hex
    uid6 = uid[:6]
    namespace = f"subagent.{subagent_type}.{uid6}"

    # 3. Зона субагента (RW): notes/ — scratch, artifacts/ — deliverable'ы.
    zone = subagents_root / uid
    for subdir in _ZONE_SUBDIRS:
        (zone / subdir).mkdir(parents=True, exist_ok=True)

    # 4. Чекпоинт ПЕРЕД работой субагента.
    _snapshot_before(ctx, f"before_{subagent_type}_{uid6}")

    writer = _writer()
    _emit(writer, {
        "type": "namespace_open",
        "namespace": namespace,
        "subagent_type": subagent_type,
        "task_id": task_id,
    })
    error_text: str | None = None
    try:
        # 5. Сборка субагента (зона — аргументом, фабрика сессионных путей не хранит).
        sub = factory.build(
            subagent_type=subagent_type,
            uid=uid,
            workspace_path=ctx.workspace_path,
            zone=zone,
        )
        _logger.debug(f"Launched subagent: {namespace}")
        # 6. Запуск + мультиплекс событий. permission_mode и session_id наследуются от хода
        #    MainAgent (HITL субагента скоупится той же сессией — резолвится её же
        #    /hitl/respond); decision_mode ХАРДКОД "accept_all" (субагент пишет только в
        #    эфемерную зону — DecisionMiddleware у него no-op; реальный confirm-gate
        #    срабатывает позже у MainAgent).
        async for ev in sub.run_stream(
            task_text,
            permission_mode=ctx.permission_mode,
            decision_mode="accept_all",
            session_id=ctx.session_id,
        ):
            _emit(writer, {
                "type": "subagent_event",
                "namespace": namespace,
                "mode": ev.mode,
                "data": ev.data,
                "task_id": task_id,
            })

        # 7. Сборка контракта: summary — финальное сообщение субагента; artifacts — листинг ФС.
        artifacts = _list_artifacts(zone, session_root)
        return {"summary": sub.final_text, "artifacts": artifacts}

    except Exception as e:  # noqa: BLE001 — краш субагента изолирован, ход MainAgent не падает
        _logger.warning("subagent %s failed", namespace, exc_info=True)
        error_text = str(e)
        return {
            "summary": f"субагент завершился с ошибкой: {error_text}",
            "artifacts": [],
            "error": error_text,
        }
    finally:
        _emit(writer, {
            "type": "namespace_close",
            "namespace": namespace,
            "task_id": task_id,
            "error": error_text,
        })
