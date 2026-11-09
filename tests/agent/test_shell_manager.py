"""Session-owned shell tasks: real execution, bounds and cancellation."""

import asyncio
import os
import shlex
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from core.agent.shell import shell_manager
from core.agent.shell.models import PreparedRun, ShellArgs, ShellError
from core.agent.shell.shell_manager import ShellManager


def python_command(script: str) -> str:
    if os.name == "nt":
        executable = sys.executable.replace("'", "''")
        quoted = script.replace("'", "''")
        return f"& '{executable}' -c '{quoted}'"
    return f"exec {shlex.quote(sys.executable)} -c {shlex.quote(script)}"


@pytest.fixture
async def manager(tmp_path: Path) -> AsyncIterator[ShellManager]:
    instance = ShellManager(tmp_path / "session")
    try:
        yield instance
    finally:
        await instance.close()


def prepared(
    tmp_path: Path, command: str, *, background: bool = False, timeout: float | None = 120
) -> PreparedRun:
    return PreparedRun(command, tmp_path, background, timeout, "main")


async def test_foreground_returns_output_and_nonzero_exit(
    manager: ShellManager,
    tmp_path: Path,
) -> None:
    result = await manager.run(prepared(tmp_path, "echo hello; exit 7"))
    assert result.status == "completed"
    assert result.exit_code == 7
    assert result.output is not None and result.output.strip() == "hello"
    assert result.eof is True
    assert result.error is None
    assert manager.status(result.task_id).finished_at is not None


async def test_background_returns_while_command_is_running(
    manager: ShellManager,
    tmp_path: Path,
) -> None:
    result = await asyncio.wait_for(
        manager.run(
            prepared(
                tmp_path,
                python_command("import time; time.sleep(300)"),
                background=True,
                timeout=None,
            )
        ),
        timeout=10,
    )
    assert result.status == "running"
    assert result.task_id in {view.task_id for view in manager.list_tasks()}
    stopped = await manager.stop(result.task_id)
    assert stopped.status == "stopped"
    assert stopped.reason == "user_stop"
    assert await manager.stop(result.task_id) == stopped


async def test_deadline_terminates_task(manager: ShellManager, tmp_path: Path) -> None:
    result = await manager.run(
        prepared(
            tmp_path,
            python_command("import time; time.sleep(300)"),
            timeout=0.1,
        )
    )
    assert result.status == "timed_out"
    assert result.finished_at is not None


async def test_output_continues_from_returned_offset(
    manager: ShellManager,
    tmp_path: Path,
) -> None:
    command = python_command("import os; os.write(1, b'x' * 51205)")
    first = await manager.run(prepared(tmp_path, command))
    assert first.output == "x" * 51200
    assert first.next_offset == 51200
    assert first.has_more is True
    second = await manager.output(first.task_id, offset=first.next_offset)
    assert second.output == "xxxxx"
    assert second.next_offset == 51205


async def test_output_limit_stops_task_even_if_shell_already_exited(tmp_path: Path) -> None:
    instance = ShellManager(tmp_path / "session", max_log_bytes=8)
    try:
        result = await instance.run(
            prepared(tmp_path, python_command("import os; os.write(1,b'x'*20)"))
        )
        assert result.status == "stopped"
        assert result.reason == "output_limit"
        assert result.output == "xxxxxxxx"
        assert (tmp_path / "session" / "shell_tasks" / f"{result.task_id}.log").stat().st_size == 8
    finally:
        await instance.close()


async def test_launch_failure_is_a_failed_task_with_diagnostic(
    manager: ShellManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refuse(_prepared: PreparedRun) -> object:
        raise ShellError("interpreter unavailable")

    monkeypatch.setattr(shell_manager, "spawn_shell", refuse)
    result = await manager.run(prepared(tmp_path, "echo hello"))
    assert result.status == "failed"
    assert result.reason == "launch_error"
    assert result.error is not None and "interpreter unavailable" in result.error


async def test_foreground_cancellation_stops_only_its_task(
    manager: ShellManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = python_command("import time; time.sleep(300)")
    background = await manager.run(prepared(tmp_path, command, background=True, timeout=None))
    entered = asyncio.Event()
    spawn = shell_manager.spawn_shell

    async def signal_spawn(request: PreparedRun):
        entered.set()
        return await spawn(request)

    monkeypatch.setattr(shell_manager, "spawn_shell", signal_spawn)
    foreground = asyncio.create_task(manager.run(prepared(tmp_path, command)))
    await asyncio.wait_for(entered.wait(), timeout=5)
    foreground.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(foreground, timeout=10)
    other = next(view for view in manager.list_tasks() if view.task_id != background.task_id)
    assert other.status == "stopped"
    assert other.reason == "turn_cancelled"
    assert manager.status(background.task_id).status == "running"


async def test_stop_during_spawn_retains_and_cleans_created_process(
    manager: ShellManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    spawn = shell_manager.spawn_shell

    async def delayed(request: PreparedRun):
        entered.set()
        await release.wait()
        return await spawn(request)

    monkeypatch.setattr(shell_manager, "spawn_shell", delayed)
    call = asyncio.create_task(
        manager.run(
            prepared(
                tmp_path,
                python_command("import time; time.sleep(300)"),
            )
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        call.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(call, timeout=10)
        (task,) = manager.list_tasks()
        assert task.status == "stopped"
        assert task.reason == "turn_cancelled"
    finally:
        release.set()
        if not call.done():
            call.cancel()
        await asyncio.gather(call, return_exceptions=True)


async def test_running_limit_is_shared_and_atomic(manager: ShellManager, tmp_path: Path) -> None:
    command = python_command("import time; time.sleep(300)")
    requests = [
        manager.run(prepared(tmp_path, command, background=True, timeout=None)) for _ in range(10)
    ]
    results = await asyncio.gather(*requests, return_exceptions=True)
    successful = [item for item in results if not isinstance(item, BaseException)]
    errors = [item for item in results if isinstance(item, BaseException)]
    assert len(successful) == 8
    assert len(errors) == 2
    assert all(isinstance(error, ShellError) for error in errors)
    assert len(manager.list_tasks()) == 8
    await manager.stop(successful[0].task_id)
    replacement = await manager.run(prepared(tmp_path, "echo slot-reused"))
    assert replacement.status == "completed"


async def test_closing_session_stops_tasks_and_rejects_new_runs(
    manager: ShellManager,
    tmp_path: Path,
) -> None:
    result = await manager.run(
        prepared(
            tmp_path,
            python_command("import time; time.sleep(300)"),
            background=True,
            timeout=None,
        )
    )
    await manager.close()
    assert manager.status(result.task_id).status == "stopped"
    assert manager.status(result.task_id).reason == "session_closed"
    await manager.close()
    with pytest.raises(ShellError):
        await manager.run(prepared(tmp_path, "echo forbidden"))


async def test_task_ids_are_scoped_to_one_manager(manager: ShellManager, tmp_path: Path) -> None:
    result = await manager.run(prepared(tmp_path, "echo hello"))
    other = ShellManager(tmp_path / "other-session")
    try:
        with pytest.raises(ShellError):
            other.status(result.task_id)
        with pytest.raises(ShellError):
            await other.output(result.task_id)
        with pytest.raises(ShellError):
            await other.stop(result.task_id)
    finally:
        await other.close()


def test_manager_prepares_requests_without_starting_processes(tmp_path: Path) -> None:
    instance = ShellManager(tmp_path / "session")
    args = ShellArgs.model_validate({"action": "run", "command": "echo hello"})
    request = instance.prepare_run(args, default_cwd=tmp_path, creator="subagent.test")
    assert request.cwd == tmp_path.resolve()
    assert request.creator == "subagent.test"
    assert request.timeout == 120
    assert instance.list_tasks() == []
    assert not (tmp_path / "session").exists()


async def test_cancelled_background_caller_leaves_session_owned_task(
    manager: ShellManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    spawn = shell_manager.spawn_shell

    async def delayed(request: PreparedRun):
        entered.set()
        await release.wait()
        return await spawn(request)

    monkeypatch.setattr(shell_manager, "spawn_shell", delayed)
    call = asyncio.create_task(
        manager.run(
            prepared(
                tmp_path,
                python_command("import time; time.sleep(300)"),
                background=True,
                timeout=None,
            )
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(call, timeout=5)
        (task,) = manager.list_tasks()
        assert task.status == "running"
    finally:
        release.set()
        if not call.done():
            call.cancel()
        await asyncio.gather(call, return_exceptions=True)
    await asyncio.wait_for(manager.close(), timeout=10)
    assert manager.status(task.task_id).reason == "session_closed"


async def test_capture_failure_stops_process_and_exposes_diagnostic(
    manager: ShellManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = shell_manager.CappedLog

    class BrokenLog(original):
        def write(self, data: bytes) -> bool:
            raise ShellError("disk unavailable")

    monkeypatch.setattr(shell_manager, "CappedLog", BrokenLog)
    command = python_command("import os,time; os.write(1,b'hello'); time.sleep(300)")
    result = await manager.run(prepared(tmp_path, command))
    assert result.status == "stopped"
    assert result.error is not None and "disk unavailable" in result.error


async def test_shell_exit_cleans_descendant_holding_output(
    manager: ShellManager,
    tmp_path: Path,
) -> None:
    if os.name == "nt":
        executable = sys.executable.replace("'", "''")
        command = (
            f"Start-Process -FilePath '{executable}' "
            "-ArgumentList '-c', '\"import time; time.sleep(300)\"' -NoNewWindow; "
            "[Console]::WriteLine('ready')"
        )
    else:
        script = (
            "import subprocess,sys; "
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(300)']); "
            "print('ready',flush=True)"
        )
        command = python_command(script)
    result = await asyncio.wait_for(manager.run(prepared(tmp_path, command)), timeout=10)
    assert result.status == "completed"
    assert result.exit_code == 0
    assert result.output is not None and result.output.strip() == "ready"


async def test_failed_cleanup_retains_task_and_stop_can_retry(
    manager: ShellManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawn = shell_manager.spawn_shell

    async def spawn_with_one_failure(request: PreparedRun):
        process = await spawn(request)
        terminate = process.terminate
        attempts = 0

        async def fail_once() -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ShellError("transient cleanup failure")
            await terminate()

        monkeypatch.setattr(process, "terminate", fail_once)
        return process

    monkeypatch.setattr(shell_manager, "spawn_shell", spawn_with_one_failure)
    task = await manager.run(
        prepared(
            tmp_path,
            python_command("import time; time.sleep(300)"),
            background=True,
            timeout=None,
        )
    )
    with pytest.raises(ShellError, match="Cleanup failed"):
        await manager.stop(task.task_id)
    assert manager.status(task.task_id).status == "running"
    assert "transient cleanup failure" in (manager.status(task.task_id).error or "")
    stopped = await manager.stop(task.task_id)
    assert stopped.status == "stopped"
    assert stopped.reason == "user_stop"


async def test_cancelled_close_still_waits_for_owned_process_cleanup(
    manager: ShellManager,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawn = shell_manager.spawn_shell
    entered = asyncio.Event()
    release = asyncio.Event()

    async def spawn_with_slow_stop(request: PreparedRun):
        process = await spawn(request)
        terminate = process.terminate

        async def delayed_stop() -> None:
            entered.set()
            await release.wait()
            await terminate()

        monkeypatch.setattr(process, "terminate", delayed_stop)
        return process

    monkeypatch.setattr(shell_manager, "spawn_shell", spawn_with_slow_stop)
    task = await manager.run(
        prepared(
            tmp_path,
            python_command("import time; time.sleep(300)"),
            background=True,
            timeout=None,
        )
    )
    closing = asyncio.create_task(manager.close())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        closing.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(closing, timeout=10)
        assert manager.status(task.task_id).status == "stopped"
        assert manager.status(task.task_id).reason == "session_closed"
    finally:
        release.set()
        await asyncio.gather(closing, return_exceptions=True)
