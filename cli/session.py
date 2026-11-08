"""Build the CLI session on top of core.

Process init (`bootstrap_process`, once per process) and the per-session build
(`new_session`) are the same init/build pair as production `core/app/server.py`, with one
fixed session. Everything comes from `core.agent.bootstrap` — the `cli` package does not
depend on a separate front end.
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
    """Process init, once: materialize procedures and the holders.

    Stores the process procedures root in `state` for `new_session` to read.
    """
    state["procedures_root"] = init_process(SESSION_ROOT)


def new_session() -> AgentSession:
    """Build a runner on a fresh MainAgent for the modes currently in state.

    Process init (`bootstrap_process`) has already run in app.main() — this is only the
    per-session build, as in SessionManager.create_session, with a fixed id.
    """
    procedures_root = state["procedures_root"]
    assert procedures_root is not None, "bootstrap_process was not called (app.main)"
    agent = build_agent_factory(procedures_root)[SESSION_PROFILE](SESSION_DIR)
    return AgentSession(
        agent,
        session_id=SESSION_ID,
        profile=SESSION_PROFILE,
        permission_mode=state["perm"],
        decision_mode=state["dec"],
    )
