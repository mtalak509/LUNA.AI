"""The live harness must close session-owned processes before its event loop ends."""

import os
from types import SimpleNamespace

import pytest

from core.agent.base import StreamEvent
from core.agent.session import AgentSession
from core.agent.shell.models import ShellArgs
from core.agent.shell.shell_manager import ShellManager
from tests.evals.harness import ScenarioRunner


@pytest.mark.parametrize("fail", [False, True])
async def test_play_turn_closes_background_tasks_on_success_and_agent_error(tmp_path, fail):
    manager = ShellManager(tmp_path)

    async def run_stream(_text, **_kwargs):
        if fail:
            raise RuntimeError("agent failed")
        yield StreamEvent("main", "custom", {"done": True})

    agent = SimpleNamespace(
        agent_config=SimpleNamespace(shell_manager=manager), run_stream=run_stream
    )
    session = AgentSession(
        agent,
        session_id="eval",
        profile="standalone",
        permission_mode="act",
        decision_mode="accept_all",
    )
    command = "Start-Sleep 300" if os.name == "nt" else "sleep 300"
    task = await manager.run(
        manager.prepare_run(
            ShellArgs(action="run", command=command, background=True),
            default_cwd=tmp_path,
            creator="main",
        )
    )
    try:
        errors = await ScenarioRunner._play_turn(session, "hello")
        assert bool(errors) is fail
        assert manager.status(task.task_id).status == "stopped"
        assert manager.status(task.task_id).reason == "session_closed"
    finally:
        await session.close()
