import asyncio
import os
import shutil
import signal

from core.agent.shell.models import PreparedRun, ShellError

STOP_GRACE_SECONDS = 2.0
POLL_INTERVAL = 0.02


class PosixShellProcess:
    """A shell and the normal descendants in its process group."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        if process.stdout is None:
            raise RuntimeError("Shell stdout must be captured")

        self._process = process
        self.stdout = process.stdout
        self._stop_lock = asyncio.Lock()
        self._terminated = False

    @property
    def pid(self) -> int:
        return self._process.pid

    async def wait(self) -> int:
        # Process has no public exit-only event; wait() may also wait for pipe EOF.
        # A descendant can keep stdout open after the shell has exited.
        while self._process.returncode is None:  # noqa: ASYNC110
            await asyncio.sleep(POLL_INTERVAL)
        return self._process.returncode

    def _group_exists(self) -> bool:
        try:
            os.killpg(self.pid, 0)
        except ProcessLookupError:
            return False
        return True

    def _signal_group(self, sig: signal.Signals) -> None:
        try:
            os.killpg(self.pid, sig)
        except ProcessLookupError:
            pass

    async def terminate(self) -> None:
        async with self._stop_lock:
            if self._terminated:
                return

            self._signal_group(signal.SIGTERM)
            deadline = asyncio.get_running_loop().time() + STOP_GRACE_SECONDS

            while self._group_exists():
                if asyncio.get_running_loop().time() >= deadline:
                    self._signal_group(signal.SIGKILL)
                    break
                await asyncio.sleep(POLL_INTERVAL)

            await self.wait()
            self._terminated = True


async def spawn_posix_shell(prepared: PreparedRun) -> PosixShellProcess:
    if os.name != "posix":
        raise ShellError("The POSIX shell backend is unavailable on this platform")

    executable = await asyncio.to_thread(shutil.which, "bash")
    if executable is None:
        raise ShellError("bash executable was not found")

    try:
        process = await asyncio.create_subprocess_exec(
            executable,
            "--noprofile",
            "--norc",
            "-c",
            prepared.command,
            cwd=prepared.cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        raise ShellError(f"Cannot start bash: {exc}") from exc

    return PosixShellProcess(process)
