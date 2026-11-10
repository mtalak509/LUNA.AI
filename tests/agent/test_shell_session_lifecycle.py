"""Session closure owns turn cancellation and shell cleanup before disk removal."""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent import session as session_module
from core.agent.base import StreamEvent
from core.agent.session import AgentSession, SessionManager
from core.agent.shell.models import PreparedRun, ShellError
from core.agent.shell.shell_manager import ShellManager
from core.app import server


class LocalAgent:
    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.agent_config = SimpleNamespace(shell_manager=ShellManager(root))
        self.started = asyncio.Event()
        self.finished = asyncio.Event()
        self.on_finish = None

    async def run_stream(self, _text, **_kwargs):
        self.started.set()
        try:
            yield StreamEvent("main", "custom", {"started": True})
            await asyncio.Event().wait()
        finally:
            if self.on_finish is not None:
                self.on_finish()
            self.finished.set()


@pytest.fixture
async def resources():
    closers = []
    sessions = []

    def agent(root):
        result = LocalAgent(root)
        closers.append(result.agent_config.shell_manager.close)
        return result

    def session(root):
        local = agent(root)
        result = AgentSession(
            local,
            session_id="session",
            profile="standalone",
            permission_mode="act",
            decision_mode="confirm",
        )
        sessions.append(result)
        return result, local

    yield SimpleNamespace(agent=agent, session=session)
    for session in sessions:
        session.cancel_turn()
        await asyncio.wait_for(session.wait_turn(), timeout=5)
    for close in closers:
        await asyncio.wait_for(close(), timeout=10)


async def start_background(manager: ShellManager, root: Path):
    command = "Start-Sleep -Seconds 300" if os.name == "nt" else "sleep 300"
    return await manager.run(PreparedRun(command, root, True, None, "main"))


async def test_close_cancels_turn_then_stops_background_tasks(tmp_path: Path, resources) -> None:
    session, agent = resources.session(tmp_path)
    shell = agent.agent_config.shell_manager
    task = await start_background(shell, tmp_path)
    observed = []
    agent.on_finish = lambda: observed.append(shell.status(task.task_id).status)
    try:
        assert session.shell_manager is shell
        session.run_turn("work")
        await asyncio.wait_for(agent.started.wait(), timeout=5)
        await session.close()
        assert agent.finished.is_set()
        assert observed == ["running"]
        assert shell.status(task.task_id).status == "stopped"
        assert shell.status(task.task_id).reason == "session_closed"
        await session.close()
        with pytest.raises(RuntimeError):
            session.run_turn("closed")
    finally:
        session.cancel_turn()
        await session.wait_turn()


async def test_closing_rejects_new_turns_before_cleanup_finishes(
    tmp_path: Path,
    resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, agent = resources.session(tmp_path)
    shell = agent.agent_config.shell_manager
    entered, release = asyncio.Event(), asyncio.Event()
    original = shell.close

    async def slow_close():
        entered.set()
        await release.wait()
        await original()

    monkeypatch.setattr(shell, "close", slow_close)
    closing = asyncio.create_task(session.close())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        with pytest.raises(RuntimeError):
            session.run_turn("too late")
    finally:
        release.set()
        await asyncio.wait_for(closing, timeout=10)


async def test_cancelled_close_waits_for_shell_cleanup(
    tmp_path: Path,
    resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, agent = resources.session(tmp_path)
    shell = agent.agent_config.shell_manager
    task = await start_background(shell, tmp_path)
    entered, release = asyncio.Event(), asyncio.Event()
    original = shell.close

    async def slow_close():
        entered.set()
        await release.wait()
        await original()

    monkeypatch.setattr(shell, "close", slow_close)
    closing = asyncio.create_task(session.close())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        closing.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, timeout=10)
        assert shell.status(task.task_id).status == "stopped"
    finally:
        release.set()
        await asyncio.gather(closing, return_exceptions=True)


async def test_delete_stops_processes_before_removing_session_files(
    tmp_path: Path,
    resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SessionManager(tmp_path, {"standalone": resources.agent})
    session_id, session = registry.create_session("standalone")
    root = tmp_path / session_id
    shell = session._agent.agent_config.shell_manager
    task = await start_background(shell, root)
    remove = session_module.shutil.rmtree

    def guarded_remove(path, *args, **kwargs):
        if Path(path) == root:
            assert shell.status(task.task_id).status == "stopped"
        return remove(path, *args, **kwargs)

    monkeypatch.setattr(session_module.shutil, "rmtree", guarded_remove)
    await registry.delete_session(session_id)
    assert registry.get_session(session_id) is None
    assert not root.exists()
    assert shell.status(task.task_id).reason == "session_closed"


async def test_failed_delete_keeps_registry_and_files_for_retry(
    tmp_path: Path,
    resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SessionManager(tmp_path, {"standalone": resources.agent})
    session_id, session = registry.create_session("standalone")
    root = tmp_path / session_id
    shell = session._agent.agent_config.shell_manager
    task = await start_background(shell, root)
    original = shell.close
    failed = False

    async def fail_once():
        nonlocal failed
        if not failed:
            failed = True
            raise ShellError("cleanup unavailable")
        await original()

    monkeypatch.setattr(shell, "close", fail_once)
    with pytest.raises(ShellError):
        await registry.delete_session(session_id)
    assert registry.get_session(session_id) is session
    assert root.exists()
    await registry.delete_session(session_id)
    assert not root.exists()
    assert shell.status(task.task_id).status == "stopped"


async def test_registry_shutdown_attempts_all_sessions_and_can_retry(
    tmp_path: Path,
    resources,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = SessionManager(tmp_path, {"standalone": resources.agent})
    first_id, first = registry.create_session("standalone")
    second_id, second = registry.create_session("standalone")
    first_shell = first._agent.agent_config.shell_manager
    second_shell = second._agent.agent_config.shell_manager
    first_task = await start_background(first_shell, tmp_path / first_id)
    second_task = await start_background(second_shell, tmp_path / second_id)
    original = first_shell.close
    failed = False

    async def fail_once():
        nonlocal failed
        if not failed:
            failed = True
            raise ShellError("first cleanup failed")
        await original()

    monkeypatch.setattr(first_shell, "close", fail_once)
    with pytest.raises(RuntimeError):
        await registry.close()
    assert second_shell.status(second_task.task_id).status == "stopped"
    with pytest.raises(RuntimeError):
        registry.create_session("standalone")
    await registry.close()
    await registry.close()
    assert first_shell.status(first_task.task_id).status == "stopped"


async def test_legacy_agent_without_shell_manager_can_close() -> None:
    session = AgentSession(
        object(),
        session_id="legacy",
        profile="standalone",
        permission_mode="act",
        decision_mode="confirm",
    )
    assert session.shell_manager is None
    await session.close()
    await session.close()


@pytest.mark.parametrize("failed_body", [False, True])
async def test_http_lifespan_stops_sessions_on_normal_and_exceptional_exit(
    tmp_path: Path,
    resources,
    monkeypatch: pytest.MonkeyPatch,
    failed_body: bool,
) -> None:
    root = tmp_path / "server"
    monkeypatch.setattr(
        server,
        "get_config",
        lambda: SimpleNamespace(
            server=SimpleNamespace(session_root=str(root)),
        ),
    )
    monkeypatch.setattr(server, "init_process", lambda path: path / "procedures")
    monkeypatch.setattr(
        server,
        "build_agent_factory",
        lambda _path: {
            "standalone": resources.agent,
        },
    )
    app = SimpleNamespace(state=SimpleNamespace())
    try:
        async with server.lifespan(app):
            session_id, session = app.state.sessions.create_session("standalone")
            shell = session._agent.agent_config.shell_manager
            task = await start_background(shell, root / session_id)
            if failed_body:
                raise ValueError("test body")
    except ValueError:
        assert failed_body
    assert shell.status(task.task_id).status == "stopped"
    assert shell.status(task.task_id).reason == "session_closed"
    assert (root / session_id).exists()
