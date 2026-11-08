from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from langchain_core.tools import ToolException
from pydantic import BaseModel, ConfigDict, Field, model_validator

DEFAULT_FOREGROUND_TIMEOUT = 120.0


class ShellArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["run", "status", "output", "stop"]
    command: str | None = None
    task_id: str | None = None

    cwd: str | None = None
    background: bool = Field(default=False, strict=True)
    timeout: float | None = Field(
        default=None,
        gt=0,
        allow_inf_nan=False,
        strict=True,
    )
    offset: int = Field(default=0, ge=0, strict=True)

    @model_validator(mode="after")
    def validate_action(self) -> "ShellArgs":
        supplied = self.model_fields_set

        if self.action == "run":
            if self.command is None or not self.command.strip():
                raise ValueError("run requires a non-empty command")

            forbidden = supplied & {"task_id", "offset"}
            if forbidden:
                names = ", ".join(sorted(forbidden))
                raise ValueError(f"The following fields are not allowed for run: {names}")

            if "cwd" in supplied and (self.cwd is None or not self.cwd.strip()):
                raise ValueError("cwd must be a non-empty path")

            if "timeout" in supplied and self.timeout is None:
                raise ValueError("timeout must be a positive number")
        else:
            if self.task_id is None or not self.task_id.strip():
                raise ValueError(f"{self.action} requires a non-empty task_id")

            forbidden = supplied & {"command", "cwd", "background", "timeout"}
            if self.action != "output" and "offset" in supplied:
                forbidden.add("offset")

            if forbidden:
                names = ", ".join(sorted(forbidden))
                raise ValueError(f"Arguments not allowed for {self.action}: {names}")

        return self


class ShellError(ToolException):
    """An error raised by the shell tool."""


@dataclass(frozen=True, slots=True)
class PreparedRun:
    command: str
    cwd: Path
    background: bool
    timeout: float | None
    creator: str


def prepare_run_args(
    args: ShellArgs,
    *,
    default_cwd: Path,
    creator: str,
) -> PreparedRun:
    if args.action != "run" or args.command is None:
        raise ShellError("Only run requests can be prepared")

    candidate = Path(args.cwd) if args.cwd is not None else default_cwd
    if not candidate.is_absolute():
        candidate = default_cwd / candidate

    try:
        cwd = candidate.resolve(strict=True)
        if not cwd.is_dir():
            raise ShellError(f"The specified directory does not exist: {cwd}")
    except (OSError, ValueError, RuntimeError) as exc:
        raise ShellError(f"Cannot resolve working directory: {candidate}") from exc

    timeout = args.timeout
    if timeout is None and not args.background:
        timeout = DEFAULT_FOREGROUND_TIMEOUT

    return PreparedRun(
        command=args.command,
        cwd=cwd,
        background=args.background,
        timeout=timeout,
        creator=creator,
    )
