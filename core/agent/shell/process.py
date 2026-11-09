import asyncio
import os
from typing import Protocol

from core.agent.shell.models import PreparedRun, ShellError
from core.agent.shell.posix_process import spawn_posix_shell
from core.agent.shell.windows_process import spawn_windows_shell


class OwnedShellProcess(Protocol):
    """A shell process interface used by session task manager."""

    @property
    def pid(self) -> int:
        """The process ID."""
        ...

    @property
    def stdout(self) -> asyncio.StreamReader:
        """The process stdout stream."""
        ...

    async def wait(self) -> int:
        """Wait for the process to exit and return the exit code."""
        ...

    async def terminate(self) -> None:
        """Terminate the process."""
        ...


async def spawn_shell(prepared: PreparedRun) -> OwnedShellProcess:
    if os.name == "nt":
        return await spawn_windows_shell(prepared)
    if os.name == "posix":
        return await spawn_posix_shell(prepared)

    raise ShellError(f"Unsupported platform: {os.name}")
