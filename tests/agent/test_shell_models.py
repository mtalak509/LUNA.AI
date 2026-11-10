"""Validation of the shell tool's action-specific input contract."""

import pytest
from pydantic import ValidationError

from core.agent.shell.models import ShellArgs


def test_run_accepts_command() -> None:
    args = ShellArgs.model_validate({"action": "run", "command": "echo hello"})

    assert args.action == "run"
    assert args.command == "echo hello"


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "run"},
        {"action": "run", "command": None},
        {"action": "run", "command": ""},
        {"action": "run", "command": "   "},
    ],
)
def test_run_requires_nonblank_command(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ShellArgs.model_validate(payload)

    assert any(error["type"] == "value_error" for error in exc_info.value.errors())


@pytest.mark.parametrize("action", ["status", "output", "stop"])
def test_management_accepts_task_id(action: str) -> None:
    args = ShellArgs.model_validate({"action": action, "task_id": "sh_123"})

    assert args.action == action
    assert args.task_id == "sh_123"


@pytest.mark.parametrize("action", ["status", "output", "stop"])
@pytest.mark.parametrize("task_id", [None, "", "   "])
def test_management_requires_nonblank_task_id(action: str, task_id: str | None) -> None:
    with pytest.raises(ValidationError):
        ShellArgs.model_validate({"action": action, "task_id": task_id})


@pytest.mark.parametrize("action", ["status", "output", "stop"])
def test_management_requires_task_id_argument(action: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        ShellArgs.model_validate({"action": action})

    assert any(error["type"] == "value_error" for error in exc_info.value.errors())


@pytest.mark.parametrize("action", ["status", "output", "stop"])
@pytest.mark.parametrize("command", [None, "echo hello"])
def test_management_rejects_command(action: str, command: str | None) -> None:
    with pytest.raises(ValidationError):
        ShellArgs.model_validate({"action": action, "task_id": "sh_123", "command": command})


@pytest.mark.parametrize("task_id", [None, "sh_123"])
def test_run_rejects_task_id(task_id: str | None) -> None:
    with pytest.raises(ValidationError):
        ShellArgs.model_validate({"action": "run", "command": "echo hello", "task_id": task_id})


def test_run_accepts_execution_options() -> None:
    args = ShellArgs.model_validate(
        {
            "action": "run",
            "command": "echo hello",
            "cwd": "../project",
            "background": True,
            "timeout": 30,
        }
    )

    assert args.cwd == "../project"
    assert args.background is True
    assert args.timeout == 30


def test_run_defaults_to_foreground_without_explicit_timeout() -> None:
    args = ShellArgs.model_validate({"action": "run", "command": "echo hello"})

    assert args.cwd is None
    assert args.background is False
    assert args.timeout is None


@pytest.mark.parametrize("timeout", [None, 0, -1, float("nan"), float("inf"), True, "30"])
def test_run_rejects_invalid_timeout(timeout: object) -> None:
    with pytest.raises(ValidationError):
        ShellArgs.model_validate({"action": "run", "command": "echo hello", "timeout": timeout})


@pytest.mark.parametrize("background", [None, 0, 1, "true"])
def test_run_rejects_nonboolean_background(background: object) -> None:
    with pytest.raises(ValidationError):
        ShellArgs.model_validate(
            {"action": "run", "command": "echo hello", "background": background}
        )


@pytest.mark.parametrize("cwd", [None, "", "   "])
def test_run_rejects_empty_explicit_cwd(cwd: str | None) -> None:
    with pytest.raises(ValidationError):
        ShellArgs.model_validate({"action": "run", "command": "echo hello", "cwd": cwd})


@pytest.mark.parametrize("action", ["status", "output", "stop"])
@pytest.mark.parametrize("option", [{"cwd": "."}, {"background": False}, {"timeout": 30}])
def test_management_rejects_execution_options(action: str, option: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ShellArgs.model_validate({"action": action, "task_id": "sh_123", **option})


@pytest.mark.parametrize("offset", [0, 23])
def test_output_accepts_byte_offset(offset: int) -> None:
    args = ShellArgs.model_validate({"action": "output", "task_id": "sh_123", "offset": offset})

    assert args.offset == offset


def test_output_defaults_to_start_of_log() -> None:
    args = ShellArgs.model_validate({"action": "output", "task_id": "sh_123"})

    assert args.offset == 0


@pytest.mark.parametrize("offset", [-1, True, 1.5, "23", None])
def test_output_rejects_invalid_offset(offset: object) -> None:
    with pytest.raises(ValidationError):
        ShellArgs.model_validate({"action": "output", "task_id": "sh_123", "offset": offset})


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "run", "command": "echo hello", "offset": 0},
        {"action": "status", "task_id": "sh_123", "offset": 0},
        {"action": "stop", "task_id": "sh_123", "offset": 0},
    ],
)
def test_other_actions_accept_default_offset(payload: dict[str, object]) -> None:
    args = ShellArgs.model_validate(payload)

    assert args.offset == 0


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "run", "command": "echo hello", "offset": 4},
        {"action": "status", "task_id": "sh_123", "offset": 4},
        {"action": "stop", "task_id": "sh_123", "offset": 4},
    ],
)
def test_other_actions_reject_nonzero_offset(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ShellArgs.model_validate(payload)
