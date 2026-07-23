"""Сборка сессии CLI поверх core (развязка от devfront).

Процессная инициализация (`bootstrap_process`, один раз на процесс) и per-session сборка
(`new_session`) — та же пара init/build, что и в проде `core/app/server.py`, но с одной
фиксированной сессией. Всё берётся напрямую из `core.agent.bootstrap` — пакет `cli`
самодостаточен и не зависит от devfront.
"""

from __future__ import annotations

from cli.config import (
    SESSION_DIR,
    SESSION_ID,
    SESSION_PROFILE,
    SESSION_ROOT,
    state,
)
from core.agent.bootstrap import build_agent_factory, init_process
from core.agent.session import AgentSession


def bootstrap_process() -> None:
    """Процессная инициализация (один раз): материализация процедур + холдеры.

    Кладёт procedures_root процесса в `state` — дальше его читает `new_session`.
    """
    state["procedures_root"] = init_process(SESSION_ROOT)


def new_session() -> AgentSession:
    """Собрать раннер поверх свежего MainAgent под текущие режимы state.

    Процессная инициализация (`bootstrap_process`) уже сделана в app.main() — здесь только
    per-session сборка, как в SessionManager.create_session, но с фиксированным id.
    """
    procedures_root = state["procedures_root"]
    assert procedures_root is not None, "bootstrap_process не вызван (app.main)"
    agent = build_agent_factory(procedures_root)[SESSION_PROFILE](SESSION_DIR)
    return AgentSession(
        agent,
        session_id=SESSION_ID,
        profile=SESSION_PROFILE,
        permission_mode=state["perm"],
        decision_mode=state["dec"],
    )
