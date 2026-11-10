"""PowerShell execution with a gated, job-owned bootstrap process."""

import asyncio
import base64
import json
import os
import shutil
import sys

from core.agent.shell.models import PreparedRun, ShellError
from core.agent.shell.win_job import WindowsJob

POLL_INTERVAL = 0.02
STARTUP_TIMEOUT = 10.0
CREATE_NEW_PROCESS_GROUP = 0x00000200

BOOTSTRAP_CODE = r"""
import json
import subprocess
import sys

sys.stderr.buffer.write(b"READY\n")
sys.stderr.buffer.flush()

if sys.stdin.buffer.read(1) != b"\x01":
    raise SystemExit(125)

try:
    child = subprocess.Popen(
        json.loads(sys.argv[1]),
        stdin=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
except (OSError, ValueError) as exc:
    message = str(exc).replace("\n", " ").replace("\r", " ")
    sys.stderr.buffer.write(b"ERROR " + message.encode("utf-8")[:2048] + b"\n")
    sys.stderr.buffer.flush()
    raise SystemExit(125)

sys.stderr.buffer.write(b"OK\n")
sys.stderr.buffer.flush()
raise SystemExit(child.wait())
"""


class WindowsShellProcess:
    """A PowerShell task owned through its bootstrap's Windows job."""

    def __init__(self, process: asyncio.subprocess.Process, job: WindowsJob) -> None:
        if process.stdout is None:
            raise RuntimeError("Shell stdout must be captured")

        self._process = process
        self._job = job
        self.stdout = process.stdout
        self._stop_lock = asyncio.Lock()
        self._terminated = False

    @property
    def pid(self) -> int:
        return self._process.pid

    async def wait(self) -> int:
        # A descendant may hold stdout after the initial command exits.
        while self._process.returncode is None:  # noqa: ASYNC110
            await asyncio.sleep(POLL_INTERVAL)
        return self._process.returncode

    async def terminate(self) -> None:
        async with self._stop_lock:
            if self._terminated:
                return
            self._job.close()
            await self.wait()
            self._terminated = True


def _powershell_argv(executable: str, command: str) -> list[str]:
    script = (
        "$OutputEncoding = [Console]::OutputEncoding = "
        "[System.Text.UTF8Encoding]::new($false)\n"
        f"{command}\n"
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return [
        executable,
        "-NoLogo",
        "-NoProfile",
        "-NonInteractive",
        "-OutputFormat",
        "Text",
        "-EncodedCommand",
        encoded,
    ]


async def _discard(reader: asyncio.StreamReader | None) -> None:
    if reader is None:
        return
    while await reader.read(65536):
        pass


async def _abort(process: asyncio.subprocess.Process | None, job: WindowsJob) -> None:
    try:
        job.close()
    finally:
        if process is not None:
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await asyncio.gather(
                _discard(process.stdout),
                _discard(process.stderr),
                process.wait(),
            )


async def _expect(reader: asyncio.StreamReader, expected: bytes) -> None:
    message = await asyncio.wait_for(reader.readline(), STARTUP_TIMEOUT)
    if message != expected:
        detail = message.decode("utf-8", errors="replace").strip()
        raise ShellError(f"PowerShell startup failed: {detail or 'control stream closed'}")


async def _start(prepared: PreparedRun, executable: str) -> WindowsShellProcess:
    job = WindowsJob()
    process: asyncio.subprocess.Process | None = None
    try:
        argv = _powershell_argv(executable, prepared.command)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-S",
            "-u",
            "-c",
            BOOTSTRAP_CODE,
            json.dumps(argv),
            cwd=prepared.cwd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=CREATE_NEW_PROCESS_GROUP,
        )
        if process.stdin is None or process.stderr is None:
            raise RuntimeError("Bootstrap control streams must be captured")

        await _expect(process.stderr, b"READY\n")
        job.assign(process.pid)
        process.stdin.write(b"\x01")
        await process.stdin.drain()
        process.stdin.close()
        await _expect(process.stderr, b"OK\n")
        return WindowsShellProcess(process, job)
    except BaseException as exc:
        await _abort(process, job)
        if isinstance(exc, (OSError, ValueError, TimeoutError, NotImplementedError)):
            raise ShellError(f"Cannot start PowerShell: {exc}") from exc
        raise


def _find_powershell() -> str | None:
    return shutil.which("pwsh") or shutil.which("powershell")


async def spawn_windows_shell(prepared: PreparedRun) -> WindowsShellProcess:
    if os.name != "nt":
        raise ShellError("The Windows shell backend requires Windows")

    executable = await asyncio.to_thread(_find_powershell)
    if executable is None:
        raise ShellError("PowerShell executable was not found")

    starting = asyncio.create_task(_start(prepared, executable))
    try:
        return await asyncio.shield(starting)
    except asyncio.CancelledError:
        try:
            owned = await starting
        except Exception:
            pass
        else:
            await _abort(owned._process, owned._job)
        raise
