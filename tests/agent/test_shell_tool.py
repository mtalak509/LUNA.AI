"""The shell tool's real invocation path and standard approval boundary."""

import asyncio
import json
import os
import shlex
import sys
from contextvars import ContextVar
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from core.agent import checkpoint
from core.agent.base import AgentRuntimeContext
from core.agent.middleware.decision import DecisionMiddleware
from core.agent.middleware.permission import PermissionMiddleware
from core.agent.middleware.tool_error import ToolErrorMiddleware
from core.agent.pool import FULL_POOL
from core.agent.shell.shell_manager import ShellManager
from core.agent.tools import assemble_pool, shell_tools


def python_command(script: str) -> str:
    if os.name == "nt":
        return "& '{}' -c '{}'".format(sys.executable.replace("'", "''"), script.replace("'", "''"))
    return f"exec {shlex.quote(sys.executable)} -c {shlex.quote(script)}"


def decode(message):
    assert isinstance(message.content, str)
    return json.loads(message.content)


@pytest.fixture
async def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workspace = tmp_path / "workspace"
    zone = tmp_path / "subagents" / "worker"
    workspace.mkdir()
    zone.mkdir(parents=True)
    manager = ShellManager(tmp_path)
    context = AgentRuntimeContext(
        permission_mode="act",
        decision_mode="accept_all",
        namespace="main",
        workspace_path=workspace,
        agent_scope="main",
        session_id="session",
        shell_manager=manager,
        checkpoints_root=tmp_path / "checkpoints",
    )
    active = ContextVar("test_shell_context", default=context)

    def get_runtime():
        return SimpleNamespace(context=active.get())

    monkeypatch.setattr(shell_tools, "get_runtime", get_runtime)
    monkeypatch.setattr(checkpoint, "get_runtime", get_runtime)
    snapshots = []

    def snapshot(_self, label):
        snapshots.append(label)
        return tmp_path / "unexpected-snapshot"

    monkeypatch.setattr(checkpoint.CheckpointManager, "snapshot", snapshot)

    async def no_approval(**_kwargs):
        raise AssertionError("This action must not request approval")

    monkeypatch.setattr("core.agent.middleware.decision.request_human", no_approval)

    async def invoke(args, ctx=None):
        token = active.set(ctx or context)
        try:
            result = await shell_tools.shell.ainvoke(args)
            return json.loads(result)
        finally:
            active.reset(token)

    async def gated(args, ctx=None):
        ctx = ctx or context
        token = active.set(ctx)
        request = SimpleNamespace(
            tool=shell_tools.shell,
            tool_call={"name": "shell", "id": uuid4().hex, "args": args, "type": "tool_call"},
            runtime=SimpleNamespace(context=ctx),
        )

        async def execute(req):
            return await shell_tools.shell.ainvoke(req.tool_call)

        async def decision(req):
            return await DecisionMiddleware().awrap_tool_call(req, execute)

        async def permission(req):
            return await PermissionMiddleware().awrap_tool_call(req, decision)

        try:
            return await ToolErrorMiddleware().awrap_tool_call(request, permission)
        finally:
            active.reset(token)

    try:
        yield SimpleNamespace(
            root=tmp_path,
            workspace=workspace,
            zone=zone,
            manager=manager,
            context=context,
            invoke=invoke,
            gated=gated,
            snapshots=snapshots,
        )
    finally:
        await manager.close()


@pytest.mark.parametrize("scope", ["main", "subagent"])
def test_one_shell_tool_is_available_only_in_act(scope: str) -> None:
    act = assemble_pool(FULL_POOL, agent_scope=scope, permission_mode="act")
    plan = assemble_pool(FULL_POOL, agent_scope=scope, permission_mode="plan")
    assert [tool.name for tool in act].count("shell") == 1
    assert "shell" not in {tool.name for tool in plan}


async def test_real_ainvoke_preserves_omitted_fields_and_nonzero_exit(env) -> None:
    result = await env.invoke({"action": "run", "command": "echo hello; exit 7"})
    assert result["status"] == "completed"
    assert result["exit_code"] == 7
    assert result["output"].strip() == "hello"
    assert result["task_id"]
    assert env.snapshots == []


@pytest.mark.parametrize("scope", ["main", "subagent"])
async def test_default_cwd_is_calling_agents_zone(env, scope: str) -> None:
    ctx = replace(env.context, agent_scope=scope, subagent_path=env.zone, namespace=scope)
    command = python_command("from pathlib import Path; Path('marker.txt').write_text('ok')")
    result = await env.invoke({"action": "run", "command": command}, ctx)
    target = env.zone if scope == "subagent" else env.workspace
    other = env.workspace if scope == "subagent" else env.zone
    assert result["status"] == "completed"
    assert (target / "marker.txt").read_text() == "ok"
    assert not (other / "marker.txt").exists()


@pytest.mark.parametrize("action", ["run", "status", "output", "stop"])
async def test_plan_blocks_every_action_before_execution(env, action: str) -> None:
    ctx = replace(env.context, permission_mode="plan", decision_mode="confirm")
    args = {"action": action}
    args.update({"command": "echo forbidden"} if action == "run" else {"task_id": "missing"})
    result = await env.gated(args, ctx)
    assert result.status == "error"
    assert env.manager.list_tasks() == []


@pytest.mark.parametrize("scope", ["main", "subagent"])
@pytest.mark.parametrize("action", ["run", "status", "output", "stop"])
@pytest.mark.parametrize("approved", [False, True])
async def test_confirm_every_action_requires_one_approval(
    env, monkeypatch, scope, action, approved
) -> None:
    ctx = replace(
        env.context,
        decision_mode="confirm",
        agent_scope=scope,
        subagent_path=env.zone,
    )
    if action == "run":
        args = {"action": "run", "command": "echo approved"}
        task_id = None
    else:
        command = "Start-Sleep 300" if os.name == "nt" else "sleep 300"
        task = await env.invoke({"action": "run", "command": command, "background": True})
        task_id = task["task_id"]
        args = {"action": action, "task_id": task_id}

    before = env.manager.list_tasks()
    calls = []

    async def confirm(**kwargs):
        # Approval occurs before any requested action has executed.
        assert env.manager.list_tasks() == before
        calls.append(kwargs)
        return approved

    monkeypatch.setattr("core.agent.middleware.decision.request_human", confirm)
    result = await env.gated(args, ctx)
    assert len(calls) == 1
    description = calls[0]["payload"]["action_description"]
    assert "shell" in description
    assert action in description
    assert (args["command"] if action == "run" else task_id) in description

    if not approved:
        assert result.status == "error"
        assert "task_id" not in result.content
        assert env.manager.list_tasks() == before
    else:
        assert result.status == "success"
        data = decode(result)
        if action == "run":
            assert data["output"].strip() == "approved"
        else:
            assert data["task_id"] == task_id
            expected = "stopped" if action == "stop" else "running"
            assert env.manager.status(task_id).status == expected
    assert env.snapshots == []


@pytest.mark.parametrize("scope", ["main", "subagent"])
async def test_accept_all_actions_do_not_prompt(env, scope) -> None:
    ctx = replace(env.context, agent_scope=scope, subagent_path=env.zone)
    command = "Start-Sleep 300" if os.name == "nt" else "sleep 300"
    started = await env.gated({"action": "run", "command": command, "background": True}, ctx)
    task_id = decode(started)["task_id"]
    for action in ("status", "output", "stop"):
        result = await env.gated({"action": action, "task_id": task_id}, ctx)
        assert result.status == "success"
        assert decode(result)["task_id"] == task_id
    assert env.manager.status(task_id).status == "stopped"
    assert env.snapshots == []


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "run"},
        {"action": "run", "command": "echo x", "timeout": None},
        {"action": "run", "command": "echo x", "cwd": "missing"},
        {"action": "status", "task_id": "x", "command": None},
    ],
)
async def test_invalid_request_has_no_side_effects(env, payload) -> None:
    result = await env.gated(payload)
    assert result.status == "error"
    assert env.manager.list_tasks() == []


async def test_missing_manager_is_an_explicit_error(env) -> None:
    result = await env.gated(
        {"action": "run", "command": "echo x"},
        replace(
            env.context,
            shell_manager=None,
            decision_mode="accept_all",
        ),
    )
    assert result.status == "error"
    assert env.manager.list_tasks() == []


async def test_large_output_keeps_envelope_and_continuation(env) -> None:
    command = python_command("import os; os.write(1,b'x'*51205)")
    first = await env.invoke({"action": "run", "command": command})
    assert first["status"] == "completed"
    assert first["output"] == "x" * 51200
    assert first["next_offset"] == 51200
    second = await env.invoke(
        {"action": "output", "task_id": first["task_id"], "offset": first["next_offset"]}
    )
    assert second["output"] == "xxxxx"
    assert second["eof"] is True
    assert not (env.workspace / ".truncated_outputs").exists()


async def test_entering_plan_while_approval_is_pending_blocks_launch(env, monkeypatch) -> None:
    ctx = replace(env.context, decision_mode="confirm")

    async def confirm(**_kwargs):
        ctx.permission_mode = "plan"
        return True

    monkeypatch.setattr("core.agent.middleware.decision.request_human", confirm)
    result = await env.gated({"action": "run", "command": "echo x"}, ctx)
    assert result.status == "error"
    assert env.manager.list_tasks() == []


async def test_disappearing_approved_directory_does_not_fall_back(env, monkeypatch) -> None:
    target = env.workspace / "gone"
    target.mkdir()

    async def confirm(**_kwargs):
        target.rmdir()
        return True

    monkeypatch.setattr("core.agent.middleware.decision.request_human", confirm)
    result = await env.gated(
        {"action": "run", "command": "echo x", "cwd": "gone"},
        replace(env.context, decision_mode="confirm"),
    )
    assert result.status == "error"
    assert "directory" in result.content.lower()
    assert env.manager.list_tasks() == []


async def test_parallel_calls_keep_their_agent_context(env, monkeypatch) -> None:
    a, b = env.root / "a", env.root / "b"
    a.mkdir()
    b.mkdir()
    arrived = 0
    both = asyncio.Event()

    async def confirm(**_kwargs):
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            both.set()
        await asyncio.wait_for(both.wait(), timeout=5)
        return True

    monkeypatch.setattr("core.agent.middleware.decision.request_human", confirm)
    command = python_command("from pathlib import Path; Path('marker.txt').write_text('ok')")
    results = await asyncio.gather(
        *[
            env.gated(
                {"action": "run", "command": command},
                replace(
                    env.context,
                    workspace_path=path,
                    namespace=name,
                    decision_mode="confirm",
                ),
            )
            for path, name in ((a, "first"), (b, "second"))
        ]
    )
    assert {decode(result)["creator"] for result in results} == {"first", "second"}
    assert (a / "marker.txt").exists()
    assert (b / "marker.txt").exists()


async def test_confirm_validation_runs_after_standard_approval(env, monkeypatch) -> None:
    calls = []

    async def approve(**kwargs):
        calls.append(kwargs)
        return True

    monkeypatch.setattr("core.agent.middleware.decision.request_human", approve)
    result = await env.gated(
        {"action": "run"},
        replace(env.context, decision_mode="confirm"),
    )
    assert len(calls) == 1
    assert result.status == "error"
    assert "command" in result.content
    assert env.manager.list_tasks() == []
