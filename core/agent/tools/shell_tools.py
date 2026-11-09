"""Shell tool using standard permission and decision middleware."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

from langgraph.runtime import get_runtime
from pydantic import ValidationError

from core.agent.shell.models import (
    ShellArgs,
    ShellError,
    ShellResult,
)
from core.agent.tools import agent_tool

if TYPE_CHECKING:
    from core.agent.base import AgentRuntimeContext


def parse_shell_args(raw: dict[str, Any]) -> ShellArgs:
    try:
        return ShellArgs.model_validate(raw)
    except ValidationError as exc:
        raise ShellError(f"Invalid shell arguments: {exc}") from exc


async def execute_shell(
    args: ShellArgs,
    context: AgentRuntimeContext,
) -> ShellResult:
    if context.permission_mode != "act":
        raise ShellError("The shell tool is available only in Act mode")

    manager = context.shell_manager
    if manager is None:
        raise ShellError("Shell manager is unavailable in this session")

    if args.action == "run":
        if context.agent_scope == "main":
            default_cwd = context.workspace_path
        elif context.agent_scope == "subagent":
            default_cwd = context.subagent_path
            if default_cwd is None:
                raise ShellError("Subagent working directory is unavailable")
        else:
            raise ShellError(f"Unsupported agent scope: {context.agent_scope}")

        prepared = await asyncio.to_thread(
            manager.prepare_run,
            args,
            default_cwd=default_cwd,
            creator=context.namespace,
        )
        return await manager.run(prepared)

    task_id = args.task_id
    if task_id is None:
        raise ShellError(f"{args.action} requires a task_id")

    if args.action == "output":
        return await manager.output(task_id, offset=args.offset)

    if args.action == "status":
        view = manager.status(task_id)
    elif args.action == "stop":
        view = await manager.stop(task_id)
    else:
        raise ShellError(f"Unsupported shell action: {args.action}")

    return ShellResult(**view.model_dump())


@agent_tool(
    "shell",
    is_write=True,
    args_schema=ShellArgs.model_json_schema(),
)
async def shell(**raw: Any) -> str:
    """Execute commands and manage shell tasks in the current session.

    Available only in Act mode for main agents and subagents.
    In Confirm mode every invocation requires user approval.

    action=run requires command. Optional cwd defaults to the calling
    agent's working directory. Each run starts an independent shell.
    background defaults to false. Foreground timeout defaults to 120
    seconds; background tasks have no default timeout. An explicit
    timeout must be a positive number of seconds.

    action=status returns task metadata.
    action=output reads a page from the optional byte offset, default 0.
    Continue reading using next_offset while has_more is true.
    action=stop stops the task and its owned processes.

    status, output and stop require task_id returned by run.
    Results are JSON containing task metadata and optional output.
    """
    args = parse_shell_args(raw)
    runtime = get_runtime()
    if runtime.context is None:
        raise ShellError("Agent runtime context is unavailable")

    context = cast("AgentRuntimeContext", runtime.context)
    result = await execute_shell(args, context)
    return result.model_dump_json()
