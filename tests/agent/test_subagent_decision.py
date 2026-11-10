"""Subagent writes use the inherited session decision mode."""

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from langchain_core.messages import ToolMessage

from core.agent.middleware.decision import DecisionMiddleware


@pytest.mark.parametrize(
    ("mode", "approved", "written"),
    [("confirm", False, False), ("confirm", True, True), ("accept_all", False, True)],
)
async def test_subagent_write_obeys_shared_decision_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    approved: bool,
    written: bool,
) -> None:
    target = tmp_path / "report.txt"
    context = SimpleNamespace(
        decision_mode=mode,
        agent_scope="subagent",
        namespace="subagent.worker.abc",
        session_id="session",
        workspace_path=tmp_path,
    )
    request = SimpleNamespace(
        tool=SimpleNamespace(metadata={"is_write": True}),
        tool_call={"name": "write", "id": "call-1", "args": {"path": "report.txt"}},
        runtime=SimpleNamespace(context=context),
    )
    approvals: list[dict] = []

    async def confirm(*, kind, payload, **kwargs):
        assert not target.exists(), "Approval must precede the write"
        approvals.append({"kind": kind, "payload": payload, **kwargs})
        return approved

    async def write(_request):
        await asyncio.to_thread(target.write_text, "written", encoding="utf-8")
        return ToolMessage(content="written", tool_call_id="call-1")

    monkeypatch.setattr("core.agent.middleware.decision.request_human", confirm)
    result = await DecisionMiddleware().awrap_tool_call(request, write)

    assert target.exists() is written
    if written:
        assert target.read_text(encoding="utf-8") == "written"
    else:
        assert result.status == "error"
    if mode == "confirm":
        assert len(approvals) == 1
        assert approvals[0]["namespace"] == "subagent.worker.abc"
        assert approvals[0]["session_id"] == "session"
        assert approvals[0]["write_marker"] is False
    else:
        assert approvals == []
