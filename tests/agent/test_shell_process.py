"""Platform selection and the public process adapter."""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.agent.shell import process as shell_process
from core.agent.shell.models import PreparedRun, ShellError


@pytest.mark.parametrize(
    ("os_name", "selected", "other"),
    [
        ("posix", "spawn_posix_shell", "spawn_windows_shell"),
        ("nt", "spawn_windows_shell", "spawn_posix_shell"),
    ],
)
async def test_dispatches_only_to_selected_backend(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    os_name: str,
    selected: str,
    other: str,
) -> None:
    prepared = PreparedRun("echo hello", tmp_path, False, 120, "test")
    handle = object()
    chosen = AsyncMock(return_value=handle)
    wrong = AsyncMock(side_effect=AssertionError("Wrong platform backend"))
    # Replace this module's OS reference without changing os.name for pathlib/pytest.
    monkeypatch.setattr(shell_process, "os", SimpleNamespace(name=os_name))
    monkeypatch.setattr(shell_process, selected, chosen)
    monkeypatch.setattr(shell_process, other, wrong)

    result = await shell_process.spawn_shell(prepared)

    assert result is handle
    chosen.assert_awaited_once_with(prepared)
    wrong.assert_not_awaited()


async def test_unsupported_platform_does_not_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    prepared = PreparedRun("echo hello", tmp_path, False, 120, "test")
    forbidden = AsyncMock(side_effect=AssertionError("No backend should run"))
    monkeypatch.setattr(shell_process, "os", SimpleNamespace(name="unsupported"))
    monkeypatch.setattr(shell_process, "spawn_posix_shell", forbidden)
    monkeypatch.setattr(shell_process, "spawn_windows_shell", forbidden)

    with pytest.raises(ShellError):
        await shell_process.spawn_shell(prepared)
    forbidden.assert_not_awaited()


@pytest.mark.skipif(os.name not in {"posix", "nt"}, reason="Unsupported platform")
async def test_public_adapter_runs_real_command(tmp_path: Path) -> None:
    prepared = PreparedRun("echo adapter", tmp_path, False, 120, "test")
    process = await shell_process.spawn_shell(prepared)
    try:
        output = await asyncio.wait_for(process.stdout.read(), timeout=15)
        code = await asyncio.wait_for(process.wait(), timeout=5)

        assert output.decode("utf-8").strip() == "adapter"
        assert code == 0
        assert process.pid > 0
    finally:
        drain = asyncio.create_task(process.stdout.read())
        try:
            await asyncio.wait_for(process.terminate(), timeout=5)
            await asyncio.wait_for(drain, timeout=5)
        finally:
            if not drain.done():
                drain.cancel()
                await asyncio.gather(drain, return_exceptions=True)
