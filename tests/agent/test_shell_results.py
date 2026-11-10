"""Stable shell result envelopes for tool and HTTP serialization."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from core.agent.shell.models import ShellResult, ShellTaskView


def task_fields(tmp_path: Path) -> dict[str, object]:
    return {
        "task_id": "sh_123",
        "command": "echo hello",
        "cwd": tmp_path,
        "creator": "main",
        "created_at": datetime(2026, 11, 8, tzinfo=UTC),
        "status": "running",
    }


def test_running_task_serializes_identity_and_empty_exit(tmp_path: Path) -> None:
    task = ShellTaskView.model_validate(task_fields(tmp_path))

    data = json.loads(task.model_dump_json())

    assert data["task_id"] == "sh_123"
    assert data["command"] == "echo hello"
    assert data["cwd"] == str(tmp_path)
    assert data["creator"] == "main"
    assert data["status"] == "running"
    assert data["exit_code"] is None
    assert data["reason"] is None
    assert data["finished_at"] is None


def test_nonzero_exit_is_a_completed_result(tmp_path: Path) -> None:
    result = ShellResult.model_validate(
        {
            **task_fields(tmp_path),
            "status": "completed",
            "exit_code": 7,
            "output": "test failed\n",
            "next_offset": 12,
            "eof": True,
            "has_more": False,
        }
    )

    data = json.loads(result.model_dump_json())

    assert data["status"] == "completed"
    assert data["exit_code"] == 7
    assert data["task_id"] == "sh_123"
    assert data["output"] == "test failed\n"
    assert data["next_offset"] == 12
    assert data["eof"] is True
    assert data["has_more"] is False


@pytest.mark.parametrize(
    "reason", ["user_stop", "turn_cancelled", "session_closed", "output_limit"]
)
def test_stopped_task_preserves_termination_reason(tmp_path: Path, reason: str) -> None:
    task = ShellTaskView.model_validate(
        {**task_fields(tmp_path), "status": "stopped", "reason": reason}
    )

    assert task.status == "stopped"
    assert task.reason == reason


@pytest.mark.parametrize("status", ["running", "completed", "stopped", "timed_out", "failed"])
def test_known_statuses_are_serializable(tmp_path: Path, status: str) -> None:
    task = ShellTaskView.model_validate({**task_fields(tmp_path), "status": status})

    assert json.loads(task.model_dump_json())["status"] == status


def test_unknown_status_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ShellTaskView.model_validate({**task_fields(tmp_path), "status": "success"})


def test_negative_continuation_offset_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        ShellResult.model_validate({**task_fields(tmp_path), "next_offset": -1})


def test_task_snapshot_cannot_be_mutated(tmp_path: Path) -> None:
    task = ShellTaskView.model_validate(task_fields(tmp_path))

    with pytest.raises(ValidationError) as exc_info:
        task.status = "stopped"

    assert any(error["type"] == "frozen_instance" for error in exc_info.value.errors())
    assert task.status == "running"
