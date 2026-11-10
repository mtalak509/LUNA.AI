"""Slash commands manage real shell tasks without an agent turn or approval."""

import os
from types import SimpleNamespace

import pytest
from prompt_toolkit.document import Document

from cli import commands
from cli.prompt import LunaCompleter
from core.agent.shell.models import ShellArgs
from core.agent.shell.shell_manager import ShellManager


@pytest.fixture
async def shell_cli(tmp_path, monkeypatch):
    manager = ShellManager(tmp_path)
    monkeypatch.setitem(commands.state, "session", SimpleNamespace(shell_manager=manager))
    monkeypatch.setitem(commands.state, "perm", "plan")
    monkeypatch.setitem(commands.state, "dec", "confirm")
    try:
        yield manager, tmp_path
    finally:
        await manager.close()


async def test_list_and_stop_without_agent_in_plan(shell_cli, capsys):
    manager, root = shell_cli
    await commands.handle_command("/shell tasks")
    assert "no shell tasks" in capsys.readouterr().out
    command = "Start-Sleep 300" if os.name == "nt" else "sleep 300"
    task = await manager.run(
        manager.prepare_run(
            ShellArgs(action="run", command=command, background=True),
            default_cwd=root,
            creator="main",
        )
    )
    assert await commands.handle_command("/shell tasks")
    output = capsys.readouterr().out
    assert task.task_id in output
    assert "running" in output
    assert command in output
    for _ in range(2):
        assert await commands.handle_command(f"/shell stop {task.task_id}")
        assert "stopped" in capsys.readouterr().out
    assert manager.status(task.task_id).status == "stopped"


@pytest.mark.parametrize("arg", ["", "run echo x", "stop", "tasks extra", "stop a b"])
async def test_invalid_usage_changes_no_tasks(shell_cli, capsys, arg):
    manager, _ = shell_cli
    await commands.handle_command(f"/shell {arg}")
    assert "usage: /shell" in capsys.readouterr().out
    assert manager.list_tasks() == []


async def test_unknown_task_reports_error(shell_cli, capsys):
    await commands.handle_command("/shell stop missing")
    assert "Unknown shell task" in capsys.readouterr().out


def test_shell_has_command_completion():
    choices = LunaCompleter().get_completions(Document("/sh"), None)
    assert "/shell" in [choice.text for choice in choices]
