"""REPL reset and exit must finish shell cleanup before discarding sessions."""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli import app, commands
from core.agent.session import AgentSession
from core.agent.shell.models import PreparedRun, ShellError
from core.agent.shell.shell_manager import ShellManager


@pytest.fixture
async def sessions(tmp_path: Path):
    created = []

    def make(root: Path):
        root.mkdir(parents=True, exist_ok=True)
        shell = ShellManager(root)
        agent = SimpleNamespace(agent_config=SimpleNamespace(shell_manager=shell))
        session = AgentSession(
            agent,
            session_id="cli",
            profile="standalone",
            permission_mode="act",
            decision_mode="confirm",
        )
        created.append(session)
        return session

    yield make
    for session in created:
        await asyncio.wait_for(session.close(), timeout=10)


async def background(session: AgentSession, root: Path):
    command = "Start-Sleep -Seconds 300" if os.name == "nt" else "sleep 300"
    return await session.shell_manager.run(PreparedRun(command, root, True, None, "main"))


async def test_reset_closes_old_session_before_wiping_files(
    tmp_path: Path,
    sessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "session"
    old = sessions(root)
    task = await background(old, root)
    state = {"session": old}
    monkeypatch.setattr(commands, "state", state)
    monkeypatch.setattr(commands, "SESSION_DIR", root)
    remove = commands._wipe_session

    def guarded_wipe(path):
        assert old.shell_manager.status(task.task_id).status == "stopped"
        remove(path)

    def replacement():
        assert list(root.iterdir()) == []
        return sessions(root)

    monkeypatch.setattr(commands, "_wipe_session", guarded_wipe)
    monkeypatch.setattr(commands, "new_session", replacement)
    assert await commands.handle_command("/reset") is True
    assert state["session"] is not old
    assert old.shell_manager.status(task.task_id).reason == "session_closed"


async def test_reset_failure_preserves_old_session_and_files(
    tmp_path: Path,
    sessions,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "session"
    old = sessions(root)
    marker = root / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    state = {"session": old}
    original_close = old.close

    async def fail():
        raise ShellError("cleanup failed")

    def forbidden_replacement():
        raise AssertionError("Must not replace a session after failed cleanup")

    monkeypatch.setattr(commands, "state", state)
    monkeypatch.setattr(commands, "SESSION_DIR", root)
    monkeypatch.setattr(commands, "new_session", forbidden_replacement)
    monkeypatch.setattr(old, "close", fail)
    try:
        with pytest.raises(ShellError):
            await commands.handle_command("/reset")
        assert state["session"] is old
        assert marker.read_text(encoding="utf-8") == "keep"
    finally:
        monkeypatch.setattr(old, "close", original_close)


@pytest.mark.parametrize("ending", ["quit", "eof", "cancel", "interrupt", "error", "init_error"])
async def test_repl_closes_current_session_on_exit(
    tmp_path: Path,
    sessions,
    monkeypatch: pytest.MonkeyPatch,
    ending: str,
) -> None:
    root = tmp_path / "session"
    session = sessions(root)
    task = await background(session, root)
    state = {"session": None, "perm": "act", "dec": "confirm"}
    monkeypatch.setattr(app, "state", state)
    monkeypatch.setattr(app, "SESSION_ROOT", tmp_path)
    monkeypatch.setattr(app, "SESSION_DIR", root)
    monkeypatch.setattr(app, "bootstrap_process", lambda: None)
    monkeypatch.setattr(app, "new_session", lambda: session)
    monkeypatch.setattr(commands, "state", state)

    class Input:
        def __init__(self):
            if ending == "init_error":
                raise ValueError("input initialization failed")
            self.interrupted = False

        async def read(self):
            if ending == "quit":
                return "/quit"
            if ending == "eof":
                raise EOFError
            if ending == "cancel":
                raise asyncio.CancelledError
            if ending == "interrupt":
                if self.interrupted:
                    return "/quit"
                self.interrupted = True
                raise KeyboardInterrupt
            raise ValueError("input failed")

    monkeypatch.setattr(app, "Prompter", Input)
    if ending in {"error", "init_error"}:
        with pytest.raises(ValueError):
            await app.main()
    else:
        await app.main()
    assert session.shell_manager.status(task.task_id).status == "stopped"
    assert session.shell_manager.status(task.task_id).reason == "session_closed"
