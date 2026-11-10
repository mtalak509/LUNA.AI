"""Resolve shell execution settings before approval or process creation."""

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from core.agent.shell.models import ShellArgs, ShellError, prepare_run_args


def test_defaults_to_agent_zone_and_foreground_deadline(tmp_path: Path) -> None:
    args = ShellArgs.model_validate({"action": "run", "command": "echo hello"})

    prepared = prepare_run_args(args, default_cwd=tmp_path, creator="main")

    assert prepared.command == "echo hello"
    assert prepared.cwd == tmp_path.resolve()
    assert prepared.background is False
    assert prepared.timeout == 120
    assert prepared.creator == "main"


def test_background_has_no_default_deadline(tmp_path: Path) -> None:
    args = ShellArgs.model_validate({"action": "run", "command": "echo hello", "background": True})

    prepared = prepare_run_args(args, default_cwd=tmp_path, creator="subagent.test.123")

    assert prepared.background is True
    assert prepared.timeout is None
    assert prepared.creator == "subagent.test.123"


@pytest.mark.parametrize("background", [False, True])
def test_explicit_timeout_overrides_mode_default(tmp_path: Path, background: bool) -> None:
    args = ShellArgs.model_validate(
        {"action": "run", "command": "echo hello", "background": background, "timeout": 0.5}
    )

    prepared = prepare_run_args(args, default_cwd=tmp_path, creator="main")

    assert prepared.timeout == 0.5


def test_relative_cwd_resolves_against_agent_zone(tmp_path: Path) -> None:
    zone = tmp_path / "zone"
    zone.mkdir()
    target = tmp_path / "other project"
    target.mkdir()
    args = ShellArgs.model_validate(
        {"action": "run", "command": "echo hello", "cwd": "../other project"}
    )

    prepared = prepare_run_args(args, default_cwd=zone, creator="main")

    assert prepared.cwd == target.resolve()


def test_absolute_cwd_can_be_outside_agent_zone(tmp_path: Path) -> None:
    zone = tmp_path / "zone"
    zone.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    args = ShellArgs.model_validate({"action": "run", "command": "echo hello", "cwd": str(target)})

    prepared = prepare_run_args(args, default_cwd=zone, creator="main")

    assert prepared.cwd == target.resolve()


@pytest.mark.parametrize("kind", ["missing", "file"])
def test_rejects_unusable_explicit_cwd(tmp_path: Path, kind: str) -> None:
    target = tmp_path / kind
    if kind == "file":
        target.write_text("not a directory", encoding="utf-8")
    args = ShellArgs.model_validate({"action": "run", "command": "echo hello", "cwd": str(target)})

    with pytest.raises(ShellError):
        prepare_run_args(args, default_cwd=tmp_path, creator="main")


def test_rejects_missing_default_directory(tmp_path: Path) -> None:
    args = ShellArgs.model_validate({"action": "run", "command": "echo hello"})

    with pytest.raises(ShellError):
        prepare_run_args(args, default_cwd=tmp_path / "missing", creator="main")


def test_rejects_invalid_path_without_leaking_path_exception(tmp_path: Path) -> None:
    args = ShellArgs.model_validate({"action": "run", "command": "echo hello", "cwd": "\0"})

    with pytest.raises(ShellError):
        prepare_run_args(args, default_cwd=tmp_path, creator="main")


@pytest.mark.parametrize("action", ["status", "output", "stop"])
def test_cannot_prepare_management_action(tmp_path: Path, action: str) -> None:
    args = ShellArgs.model_validate({"action": action, "task_id": "sh_123"})

    with pytest.raises(ShellError):
        prepare_run_args(args, default_cwd=tmp_path, creator="main")


def test_preparation_preserves_command_and_does_not_execute_it(tmp_path: Path) -> None:
    command = "  echo hello > marker.txt  "
    args = ShellArgs.model_validate({"action": "run", "command": command})

    prepared = prepare_run_args(args, default_cwd=tmp_path, creator="main")

    assert prepared.command == command
    assert list(tmp_path.iterdir()) == []


def test_prepared_command_cannot_change_after_preparation(tmp_path: Path) -> None:
    args = ShellArgs.model_validate({"action": "run", "command": "echo approved"})
    prepared = prepare_run_args(args, default_cwd=tmp_path, creator="main")

    with pytest.raises(FrozenInstanceError):
        prepared.command = "echo different"

    assert prepared.command == "echo approved"
