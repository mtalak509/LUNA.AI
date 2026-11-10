"""Windows execution plus the platform-independent startup gate protocol."""

import asyncio
import json
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from core.agent.shell.models import PreparedRun, ShellError
from core.agent.shell.windows_process import (
    BOOTSTRAP_CODE,
    WindowsShellProcess,
    spawn_windows_shell,
)


@asynccontextmanager
async def bootstrap(command: list[str]) -> AsyncIterator[asyncio.subprocess.Process]:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-S",
        "-u",
        "-c",
        BOOTSTRAP_CODE,
        json.dumps(command),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        yield process
    finally:
        if process.returncode is None:
            process.kill()
        await asyncio.wait_for(process.communicate(), timeout=5)


async def test_bootstrap_waits_for_gate_before_executing_command(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    script = f"from pathlib import Path; Path({str(marker)!r}).write_text('started')"
    async with bootstrap([sys.executable, "-c", script]) as process:
        assert process.stderr is not None
        assert process.stdin is not None
        assert await asyncio.wait_for(process.stderr.readline(), timeout=5) == b"READY\n"
        assert not marker.exists()
        process.stdin.write(b"\x01")
        await process.stdin.drain()
        process.stdin.close()
        assert await asyncio.wait_for(process.stderr.readline(), timeout=5) == b"OK\n"
        assert await asyncio.wait_for(process.wait(), timeout=5) == 0
        assert marker.read_text() == "started"


async def test_bootstrap_aborts_when_gate_closes_without_permission(tmp_path: Path) -> None:
    marker = tmp_path / "started"
    script = f"from pathlib import Path; Path({str(marker)!r}).touch()"
    async with bootstrap([sys.executable, "-c", script]) as process:
        assert process.stderr is not None
        assert process.stdin is not None
        assert await asyncio.wait_for(process.stderr.readline(), timeout=5) == b"READY\n"
        process.stdin.close()
        assert await asyncio.wait_for(process.wait(), timeout=5) == 125
        assert not marker.exists()


async def test_bootstrap_merges_child_streams_and_preserves_exit() -> None:
    script = "import os; os.write(1,b'out\\n'); os.write(2,b'err\\n'); raise SystemExit(7)"
    async with bootstrap([sys.executable, "-c", script]) as process:
        assert process.stderr is not None
        assert process.stdin is not None
        assert process.stdout is not None
        assert await asyncio.wait_for(process.stderr.readline(), timeout=5) == b"READY\n"
        process.stdin.write(b"\x01")
        await process.stdin.drain()
        process.stdin.close()
        assert await asyncio.wait_for(process.stderr.readline(), timeout=5) == b"OK\n"
        assert await asyncio.wait_for(process.stdout.read(), timeout=5) == b"out\nerr\n"
        assert await asyncio.wait_for(process.wait(), timeout=5) == 7


async def test_bootstrap_reports_launch_failure_on_control_stream(tmp_path: Path) -> None:
    async with bootstrap([str(tmp_path / "missing-executable")]) as process:
        assert process.stderr is not None
        assert process.stdin is not None
        assert process.stdout is not None
        assert await asyncio.wait_for(process.stderr.readline(), timeout=5) == b"READY\n"
        process.stdin.write(b"\x01")
        await process.stdin.drain()
        process.stdin.close()
        error = await asyncio.wait_for(process.stderr.readline(), timeout=5)
        assert error.startswith(b"ERROR ")
        assert await asyncio.wait_for(process.stdout.read(), timeout=5) == b""
        assert await asyncio.wait_for(process.wait(), timeout=5) == 125


@pytest.mark.skipif(os.name == "nt", reason="Unsupported-platform behavior")
async def test_windows_backend_rejects_other_platforms(tmp_path: Path) -> None:
    prepared = PreparedRun("echo hello", tmp_path, False, 120, "test")
    with pytest.raises(ShellError):
        await spawn_windows_shell(prepared)


@asynccontextmanager
async def windows_running(command: str, cwd: Path) -> AsyncIterator[WindowsShellProcess]:
    process = await spawn_windows_shell(PreparedRun(command, cwd, False, 120, "test"))
    try:
        yield process
    finally:
        drain = asyncio.create_task(process.stdout.read())
        try:
            await asyncio.wait_for(process.terminate(), timeout=5)
            await asyncio.wait_for(drain, timeout=5)
        finally:
            if not drain.done():
                drain.cancel()
                await asyncio.gather(drain, return_exceptions=True)


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows and PowerShell")
async def test_powershell_returns_utf8_and_nonzero_exit(tmp_path: Path) -> None:
    async with windows_running("[Console]::WriteLine('Привет'); exit 7", tmp_path) as process:
        output = await asyncio.wait_for(process.stdout.read(), timeout=10)
        code = await asyncio.wait_for(process.wait(), timeout=5)

    assert output.decode("utf-8").strip() == "Привет"
    assert code == 7


@pytest.mark.skipif(os.name != "nt", reason="Requires Windows and PowerShell")
async def test_powershell_stop_closes_output_and_is_repeatable(tmp_path: Path) -> None:
    async with windows_running(
        "[Console]::WriteLine('ready'); Start-Sleep 300", tmp_path
    ) as process:
        assert (await asyncio.wait_for(process.stdout.readline(), timeout=10)).strip() == b"ready"
        await asyncio.wait_for(process.terminate(), timeout=5)
        assert await asyncio.wait_for(process.stdout.read(), timeout=5) == b""
        await asyncio.wait_for(process.terminate(), timeout=1)
