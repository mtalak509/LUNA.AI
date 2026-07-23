"""
  Холдер реестра HITL-futures + общий async-хелпер `request_human`.

  «Розетка» для HITL в обход графа — прямой аналог `_clients.py`/`_subagents.py`:
  любой примитив (`ask_user`/`select_from_options`) или `DecisionMiddleware` (программный
  gate подтверждения write-тулов) создаёт `asyncio.Future`, эмитит событие наружу и
  паркуется на нём; резолв приходит извне графа (HTTP `/hitl/respond` или REPL) через
  `resolve(hitl_id, value)`.

  Инициализируется один раз на старте процесса (`set_hitl_registry`): прод — `lifespan`
  агентского сервера; дев — `devfront/session.py` / REPL.
  """

from __future__ import annotations

import asyncio
import logging
import json
import time
from uuid import uuid4
from typing import Any

from langchain_core.tools import ToolException
from langgraph.config import get_stream_writer

_logger = logging.getLogger(__name__)

_MARKER_REL = ".runtime/hitl_pending.json"


class HitlError(ToolException):
    """Ошибка HITL-канала (напр. резолв неизвестного hitl_id). Как ToolException —
      внутри тула уходит модели для self-correction; в `/hitl/respond` ловится и
      маппится в HTTP 404."""

    
class HitlRegistry:
    """Реестр висячих HITL-futures. Один на процесс; записи скоупятся session_id (15.3)."""

    def __init__(self,) -> None:
        self._pending: dict[str, tuple[asyncio.Future[Any], str]] = {}

    def register(self, session_id: str) -> tuple[str, asyncio.Future[Any]]:
        """Зарегистрировать новое HITL-событие своей сессии. Вызывается из `request_human`."""
        hitl_id = uuid4().hex
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[hitl_id] = (future, session_id)
        return hitl_id, future

    def resolve(self, hitl_id: str, value: Any, session_id: str) -> None:
        """Резолвить HITL-событие. Вызывается извне графа (HTTP `/hitl/respond` или REPL).

        Несовпадение сессии == неизвестный id (одна ошибка/формулировка — существование
        чужого hitl_id не раскрываем); запись при этом НЕ трогается, законный владелец
        сможет резолвнуть её позже.
        """
        entry = self._pending.get(hitl_id)
        if entry is None or entry[1] != session_id:
            raise HitlError(f"Unknown hitl_id: {hitl_id!r}")
        future, _ = self._pending.pop(hitl_id)
        if not future.done():
            future.set_result(value)

    def pending_ids(self, session_id: str | None = None) -> list[str]:
        """Список нерезолвленных hitl_id: своей сессии или всего процесса (None, для /health)."""
        return [
            hid for hid, (_, sid) in self._pending.items()
            if session_id is None or sid == session_id
        ]

    
_registry: HitlRegistry | None = None


def set_hitl_registry(registry: HitlRegistry) -> None:
    """Установить холдер. Зовётся один раз на старте процесса (lifespan / дев-вход)."""
    global _registry
    _registry = registry


def get_hitl_registry() -> HitlRegistry:
    """Получить холдер. При отсутствии — выбрасывает `ToolException`."""
    if _registry is None:
        raise ToolException(
            "HITL-реестр не инициализирован: set_hitl_registry(...) не был вызван на старте процесса"
        )
    return _registry

def _emit_hitl_event(payload: dict) -> None:
    """Эмитнуть HITL-событие в custom-стрим для live-preview UI. Best-effort — не роняет тул."""
    try:
        writer = get_stream_writer()
    except Exception:  # noqa: BLE001 — нет графа/писателя → собый просто не будет
        return
    try:
        writer(payload)
    except Exception:  # noqa: BLE001
        _logger.debug("HITL event emit skipped", exc_info=True)


async def request_human(
    kind: str,
    payload: dict,
    *,
    namespace: str,
    session_id: str,
    workspace_path,
    write_marker: bool
) -> Any:
    """Общий механизм HITL: зарегистрировать future, эмитнуть событие, припарковаться.

    kind — "ask" | "select" | "confirm" (определяет тип события). `payload` —
    специфичные для примитива поля (question/options/action_description/…), уходят в
    событие как есть. `namespace` — origin (main | subagent.*). `session_id` — скоуп
    записи в реестре (15.3): резолв возможен только из своей сессии; вызывающий берёт
    его из runtime-контекста хода. `write_marker` — только main-инициированный HITL
    пишет crash-маркер (субагент эфемерен).

    Возвращает `value`, которым резолвнули future (`resolve(hitl_id, value, session_id)`).
    """
    registry = get_hitl_registry()
    hitl_id, future = registry.register(session_id)

    marker_path = None
    if write_marker:
        marker_path = workspace_path / _MARKER_REL
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(
            json.dumps({
                "hitl_id": hitl_id,
                "kind": kind,
                "payload": payload,
                "ts": time.time()
                },
            ensure_ascii=False
            ),
        encoding="utf-8"
        )

    ev_type = "hitl_confirm" if kind == "confirm" else "hitl_question"
    
    _emit_hitl_event({
        "type": ev_type,
        "hitl_id": hitl_id,
        "namespace": namespace,
        "kind": kind,
        **payload,
    })

    try:
        return await future
    finally:
        registry._pending.pop(hitl_id, None)  # на случай отмены/ошибки — не течём
        if marker_path is not None:
            marker_path.unlink(missing_ok=True)

