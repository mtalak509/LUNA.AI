"""User shell controls operate on the session's real manager, independently of agent modes."""

import os
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient

from core.agent.shell.models import ShellArgs
from core.agent.shell.shell_manager import ShellManager
from core.app.server import create_app


@pytest.fixture
async def shell_api(tmp_path):
    manager = ShellManager(tmp_path)
    session = SimpleNamespace(shell_manager=manager, permission_mode="plan", is_busy=True)
    app = create_app()
    app.state.sessions = SimpleNamespace(
        get_session=lambda sid: session if sid == "current" else None
    )
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
        try:
            yield client, manager, tmp_path
        finally:
            await manager.close()


async def test_list_and_idempotent_stop_while_session_is_busy_in_plan(shell_api):
    client, manager, root = shell_api
    command = "Start-Sleep 300" if os.name == "nt" else "sleep 300"
    task = await manager.run(
        manager.prepare_run(
            ShellArgs(action="run", command=command, background=True),
            default_cwd=root,
            creator="subagent.worker",
        )
    )
    response = await client.get("/sessions/current/shell/tasks")
    assert response.status_code == 200
    assert response.json()[0]["task_id"] == task.task_id
    assert response.json()[0]["creator"] == "subagent.worker"
    assert "pid" not in response.json()[0]
    for _ in range(2):
        stopped = await client.post(f"/sessions/current/shell/tasks/{task.task_id}/stop")
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "stopped"
        assert stopped.json()["reason"] == "user_stop"
    assert manager.status(task.task_id).status == "stopped"


@pytest.mark.parametrize(
    "path",
    [
        "/sessions/missing/shell/tasks",
        "/sessions/missing/shell/tasks/missing/stop",
        "/sessions/current/shell/tasks/missing/stop",
    ],
)
async def test_unknown_session_or_task_returns_404(shell_api, path):
    client, _, _ = shell_api
    response = await (client.post(path) if path.endswith("/stop") else client.get(path))
    assert response.status_code == 404


async def test_other_sessions_task_cannot_be_stopped(shell_api, tmp_path):
    client, _, _ = shell_api
    other = ShellManager(tmp_path / "other")
    try:
        task = await other.run(
            other.prepare_run(
                ShellArgs(action="run", command="echo done"), default_cwd=tmp_path, creator="main"
            )
        )
        response = await client.post(f"/sessions/current/shell/tasks/{task.task_id}/stop")
        assert response.status_code == 404
    finally:
        await other.close()
