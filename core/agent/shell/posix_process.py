import asyncio
import os
import shutil
import signal
from pathlib import Path

from core.agent.shell.models import PreparedRun, ShellError

STOP_GRACE_SECONDS = 2.0
POLL_INTERVAL = 0.02


def _proc_start_time(pid: int) -> int | None:
    """Linux starttime for pid, or None when /proc no longer names that process."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_bytes()
    except OSError:
        return None
    end = raw.rfind(b")")
    if end < 0:
        return None
    fields = raw[end + 2 :].split()
    try:
        # Field 22 of the whole stat line; field 3 is the first token after comm.
        return int(fields[19])
    except (IndexError, ValueError):
        return None


class PosixShellProcess:
    """A shell and the normal descendants in its process group."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        if process.stdout is None:
            raise RuntimeError("Shell stdout must be captured")

        self._process = process
        self.stdout = process.stdout
        self._stop_lock = asyncio.Lock()
        self._terminated = False
        self._start_time = _proc_start_time(process.pid)

    @property
    def pid(self) -> int:
        return self._process.pid

    async def wait(self) -> int:
        # Process has no public exit-only event; wait() may also wait for pipe EOF.
        # A descendant can keep stdout open after the shell has exited.
        while self._process.returncode is None:  # noqa: ASYNC110
            await asyncio.sleep(POLL_INTERVAL)
        return self._process.returncode

    def _pid_still_ours(self) -> bool:
        """False when the leader pid was reused by an unrelated process."""
        if self._start_time is None:
            return True
        current = _proc_start_time(self.pid)
        # The leader has been reaped. Descendants may still use this process group id.
        if current is None:
            return True
        return current == self._start_time

    def _group_exists(self) -> bool:
        if not self._pid_still_ours():
            return False
        try:
            os.killpg(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            # A member we cannot signal (for example after setuid) must not pin the task.
            return False
        return True

    def _signal_group(self, sig: signal.Signals) -> None:
        if not self._pid_still_ours():
            return
        try:
            os.killpg(self.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    def _signal_leader(self, sig: signal.Signals) -> None:
        current = _proc_start_time(self.pid)
        if self._start_time is None or current != self._start_time:
            return
        try:
            os.kill(self.pid, sig)
        except (ProcessLookupError, PermissionError):
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
            if self._process.returncode is None:
                self._signal_leader(signal.SIGKILL)

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
