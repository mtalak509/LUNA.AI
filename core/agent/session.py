"""
Раннер `AgentSession` + реестр сессий `SessionManager` 

`AgentSession` — развязывает ход агента и потребителя событий. Ход MainAgent крутится
как `asyncio.Task` (`_pump`), события льются в `asyncio.Queue`;

`SessionManager` — тонкий владелец реестра сессий процесса и корня `/session/` на диске
(session = поддиректория `/session/<id>/`), БЕЗ HTTP: маршруты `/sessions/*` поверх него —
server.py.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.agent.base import BaseAgent, StreamEvent
from core.agent.tools._hitl import get_hitl_registry

_logger = logging.getLogger(__name__)


class AgentSession:
    """Обвязка вокруг MainAgent: один активный ход как Task + очередь событий сессии."""

    def __init__(
        self,
        agent: BaseAgent,
        *,
        session_id: str,
        profile: str,
        permission_mode: str,
        decision_mode: str,
        ) -> None:

        self._agent = agent
        self.profile = profile
        self.session_id = session_id
        self.permission_mode = permission_mode
        self.decision_mode = decision_mode
        self._queue: asyncio.Queue[StreamEvent] = asyncio.Queue()
        self._turn_task: asyncio.Task[None] | None = None
        self._last_activity: datetime = datetime.now(timezone.utc)
        self._turn_counter = 0

    @property
    def is_busy(self) -> bool:
        return self._turn_task is not None and not self._turn_task.done()

    @property
    def last_activity(self) -> str:
        return self._last_activity.isoformat()

    def _update_last_activity(self) -> None:
        self._last_activity = datetime.now(timezone.utc)

    def run_turn(self, text: str, pointer: str | None = None) -> str:
        """Запустить ход как фоновый Task; НЕ блокирует. Возвращает turn_id.

        Один активный ход на сессию (история агента — общая мутируемая копия, конкурентные
        ходы её бы гонкой испортили). Вывод хода — через `events()`; ход может припарковаться
        на HITL, который резолвится отдельным `resolve_hitl` (потому не ждём завершения тут).
        """
        if self.is_busy:
            raise RuntimeError("Another turn is already running")
        self._turn_counter += 1
        turn_id = f"turn-{self._turn_counter}"
        self._turn_task = asyncio.create_task(self._pump(turn_id, text, pointer))
        return turn_id

    async def _pump(self, turn_id: str, text: str, pointer: str | None = None) -> None:
        """Внутренний ход MainAgent: парсит вход, запускает LLM, эмитит события."""
        try:
            async for event in self._agent.run_stream(
                text,
                permission_mode=self.permission_mode,
                decision_mode=self.decision_mode,
                session_id=self.session_id,
                pointer=pointer,
            ):
                self._queue.put_nowait(event)
                self._update_last_activity()
        except asyncio.CancelledError:
            _logger.info(f"Turn {turn_id} cancelled")
            self._queue.put_nowait(StreamEvent(
                namespace="main",
                mode="control",
                data={
                    "type": "turn_cancelled",
                    "turn_id": turn_id,
                }),
            )
        except Exception as e:
            _logger.exception(f"Turn {turn_id} failed", exc_info=True)
            self._queue.put_nowait(StreamEvent(
                namespace="main",
                mode="error",
                data={
                    "error": str(e),
                    "turn_id": turn_id,
                }),
            )
        finally:
            self._queue.put_nowait(
                  StreamEvent(namespace="main", mode="control", data={"type": "turn_done", "turn_id": turn_id})
              )
            self._update_last_activity()

    def cancel_turn(self) -> bool:
        """Отменить активный ход. Вызывается извне (HTTP `/sessions/{id}/cancel` / REPL).

        Отменяет Task хода: CancelledError всплывает через run_stream, отрабатывают все finally
        (HITL-future/маркер чистятся, граф закрывается), _pump эмитит turn_cancelled + turn_done.
        Гасит всё дерево хода разом, включая субагента.
        """
        if self._turn_task is None or self._turn_task.done():
            return False
        self._turn_task.cancel()
        self._update_last_activity()
        return True

    async def wait_turn(self) -> None:
        """Дождаться завершения хода."""
        if self._turn_task is not None and not self._turn_task.done():
            await asyncio.shield(asyncio.wait({self._turn_task}))

    def resolve_hitl(self, hitl_id: str, value: Any) -> None:
        """Разрешить висящий HITL-вопрос ответом извне (HTTP `/hitl/respond` / REPL).

        Разблокирует припаркованный тул независимо от того, какой агент создал future
        (main или субагент — тот наследует session_id хода main). Реестр процессный,
        но записи скоупятся сессией (15.3): чужой/неизвестный id → HitlError.
        """
        get_hitl_registry().resolve(hitl_id, value, self.session_id)

    async def events(self):
        """Бесконечный async-генератор событий сессии (один SSE-стрим на весь сеанс).

        Не завершается на `turn_done` — тот отдаётся наружу как рядовое control-событие,
        маркирующее конец хода. `/events` открывается один раз и переживает много ходов.
        """
        while True:
            ev = await self._queue.get()
            yield ev


# --- SessionManager ----------------------------------------------------------------------------

#Defaults
_DEFAULT_PERMISSION_MODE = "act"
_DEFAULT_DECISION_MODE = "confirm"


@dataclass
class _SessionRecord:
    session: AgentSession
    created_at: str  # ISO-8601


class SessionManager:
    """Реестр сессий процесса и корня `/session/` на диске.

    `session_id` — имя поддиректории в `/session/` (uuid4.hex - гарантировано уникальное)
    """

    def __init__(
        self,
        root_path: Path,
        agent_factories: dict[str, Callable[[Path], BaseAgent]]
        ) -> None:
        self._root_path = root_path
        self._agent_factories = agent_factories
        self._records: dict[str, _SessionRecord] = {}

    def create_session(self, profile: str) -> tuple[str, AgentSession]:
        """Создать сессию: минт id, директория на диске, сборка агента.

        `exist_ok=False` — paranoia-check от коллизии uuid и от затирания живой
        директории
        """
        try:
            agent_factory = self._agent_factories[profile]
        except KeyError:
            raise ValueError(f"Invalid profile: {profile}") from None
        session_id = uuid.uuid4().hex
        session_root = self._root_path / session_id
        session_root.mkdir(parents=True, exist_ok=False)
        agent = agent_factory(session_root)
        session = AgentSession(
            agent=agent,
            session_id=session_id,
            profile=profile,
            permission_mode=_DEFAULT_PERMISSION_MODE,
            decision_mode=_DEFAULT_DECISION_MODE,
        )

        self._records[session_id] = _SessionRecord(
            session=session,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        return session_id, session

    def get_session(self, session_id: str) -> AgentSession | None:
        """Получить сессию по id."""
        record = self._records.get(session_id)
        return record.session if record is not None else None

    def list_sessions(self) -> list[dict[str, Any]]:
        """Перечень сессий для GET `/sessions`и переключения сессий на фронте"""

        return [
            {
                'session_id': session_id,
                'profile': record.session.profile,
                'is_busy': record.session.is_busy,
                'created_at': record.created_at,
                'last_activity': record.session.last_activity,
                'decision_mode': record.session.decision_mode,
                'permission_mode': record.session.permission_mode,
            }
            for session_id, record in self._records.items()
        ]

    async def delete_session(self, session_id: str) -> None:
        """Убить сессию: погасить ход, убрать из реестра и стереть диск"""
        record = self._records[session_id]
        record.session.cancel_turn()
        await record.session.wait_turn()
        del self._records[session_id]
        shutil.rmtree(self._root_path / session_id, ignore_errors=True)
        
        