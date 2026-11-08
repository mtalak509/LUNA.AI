"""Real bash processes: streams, environment and owned descendants."""

import asyncio
import os
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from core.agent.shell.posix_process import PosixShellProcess, spawn_posix_shell

from core.agent.shell.models import PreparedRun, ShellError

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process backend")


@asynccontextmanager
async def running(command: str, cwd: Path) -> AsyncIterator[PosixShellProcess]:
    prepared = PreparedRun(command, cwd, False, 120, "test")
    process = await spawn_posix_shell(prepared)
    try:
        yield process
    finally:
        drain = asyncio.create_task(process.stdout.read())
        try:
            await asyncio.wait_for(process.terminate(), timeout=5)
            await asyncio.wait_for(drain, timeout=2)
        finally:
            if not drain.done():
                drain.cancel()
                await asyncio.gather(drain, return_exceptions=True)


async def test_captures_stdout_and_stderr_in_one_stream(tmp_path: Path) -> None:
    async with running("printf 'out\\n'; printf 'err\\n' >&2", tmp_path) as process:
        output = await asyncio.wait_for(process.stdout.read(), timeout=3)
        code = await asyncio.wait_for(process.wait(), timeout=3)

    assert output == b"out\nerr\n"
    assert code == 0


async def test_runs_in_directory_with_spaces(tmp_path: Path) -> None:
    cwd = tmp_path / "my project"
    cwd.mkdir()
    async with running("pwd", cwd) as process:
        output = await asyncio.wait_for(process.stdout.read(), timeout=3)

    assert Path(os.fsdecode(output).strip()) == cwd


async def test_inherits_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LUNA_SHELL_TEST_VALUE", "inherited")
    async with running('printf "%s" "$LUNA_SHELL_TEST_VALUE"', tmp_path) as process:
        output = await asyncio.wait_for(process.stdout.read(), timeout=3)

    assert output == b"inherited"


async def test_stdin_is_closed(tmp_path: Path) -> None:
    command = "if read -r value; then printf unexpected; else printf eof; fi"
    async with running(command, tmp_path) as process:
        output = await asyncio.wait_for(process.stdout.read(), timeout=3)

    assert output == b"eof"


async def test_exit_code_is_preserved(tmp_path: Path) -> None:
    async with running("exit 7", tmp_path) as process:
        code = await asyncio.wait_for(process.wait(), timeout=3)

    assert code == 7


async def test_invocations_do_not_share_cd_or_exports(tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    command = f"export LUNA_SHELL_LOCAL=changed; cd {shlex.quote(str(other))}"
    async with running(command, tmp_path) as first:
        assert await asyncio.wait_for(first.wait(), timeout=3) == 0

    async with running('printf "%s\\n" "${LUNA_SHELL_LOCAL-unset}"; pwd', tmp_path) as second:
        output = await asyncio.wait_for(second.stdout.read(), timeout=3)

    assert output.decode().splitlines() == ["unset", str(tmp_path)]


async def test_shell_exit_is_observable_while_child_holds_output(tmp_path: Path) -> None:
    async with running("sleep 300 & printf 'ready\\n'", tmp_path) as process:
        assert await asyncio.wait_for(process.stdout.readline(), timeout=3) == b"ready\n"
        assert await asyncio.wait_for(process.wait(), timeout=3) == 0
        await asyncio.wait_for(process.terminate(), timeout=5)
        assert await asyncio.wait_for(process.stdout.read(), timeout=2) == b""


async def test_force_stop_kills_term_resistant_child_and_is_idempotent(tmp_path: Path) -> None:
    child = "trap '' TERM; printf 'ready\\n'; while :; do sleep 1; done"
    async with running(f"bash -c {shlex.quote(child)} & wait", tmp_path) as process:
        assert await asyncio.wait_for(process.stdout.readline(), timeout=3) == b"ready\n"
        await asyncio.wait_for(process.terminate(), timeout=5)
        assert await asyncio.wait_for(process.stdout.read(), timeout=2) == b""
        await asyncio.wait_for(process.terminate(), timeout=1)


async def test_missing_bash_reports_tool_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    prepared = PreparedRun("echo hello", tmp_path, False, 120, "test")

    with pytest.raises(ShellError):
        await spawn_posix_shell(prepared)
