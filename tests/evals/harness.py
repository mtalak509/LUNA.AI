"""Bridge between a live LUNA session and DeepEval: run a scenario, keep its artifacts."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from deepeval.dataset import Golden
from deepeval.test_case import LLMTestCase, ToolCall
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from core.agent.bootstrap import build_session_agent, init_process
from core.agent.messages import MessageStore
from core.agent.session import AgentSession


@dataclass
class ScenarioRun:
    """Everything a scenario produced: what the agent said, called and left on disk."""

    input: str
    final_text: str
    tool_calls: list[ToolCall]
    errors: list[str]
    workspace: Path
    artifacts: Path

    @property
    def tool_names(self) -> list[str]:
        return [call.name for call in self.tool_calls]

    def to_test_case(self, golden: Golden) -> LLMTestCase:
        return LLMTestCase(
            input=self.input,
            actual_output=self.final_text,
            tools_called=self.tool_calls,
            expected_tools=golden.expected_tools,
        )


@dataclass
class ScenarioRunner:
    """Runs one golden through a fresh live session in `act` / `accept_all` mode.

    The session lives under `<runs_root>/<scenario>/` and is left there after the run, so the
    transcript and the resulting workspace can be read afterwards (by a human or by Claude as
    the judge). The folder is wiped at the start of each run.
    """

    runs_root: Path
    workspaces_root: Path
    permission_mode: str = "act"
    decision_mode: str = "accept_all"
    _session_id: str = field(default="eval", init=False)

    def run(self, golden: Golden) -> ScenarioRun:
        metadata = golden.additional_metadata or {}
        scenario = metadata["scenario"]
        root = self.runs_root / scenario
        shutil.rmtree(root, ignore_errors=True)
        session_root = root / self._session_id
        workspace = session_root / "workspace"
        shutil.copytree(self.workspaces_root / metadata["workspace"], workspace)

        procedures_root = init_process(root)
        session = AgentSession(
            build_session_agent(session_root, procedures_root),
            session_id=self._session_id,
            profile="standalone",
            permission_mode=self.permission_mode,
            decision_mode=self.decision_mode,
        )
        errors = asyncio.run(self._play_turn(session, golden.input))

        messages = MessageStore(workspace).load()
        run = ScenarioRun(
            input=golden.input,
            final_text=_final_text(messages),
            tool_calls=_tool_calls(messages),
            errors=errors,
            workspace=workspace,
            artifacts=root,
        )
        self._save_transcript(run, metadata)
        return run

    @staticmethod
    async def _play_turn(session: AgentSession, text: str) -> list[str]:
        session.run_turn(text)
        await session.wait_turn()
        errors = []
        events = session.events()
        while True:
            event = await events.__anext__()
            if event.mode == "error":
                errors.append(str(event.data.get("error")))
            if event.mode == "control" and event.data.get("type") == "turn_done":
                return errors

    @staticmethod
    def _save_transcript(run: ScenarioRun, metadata: dict[str, Any]) -> None:
        transcript = {
            "input": run.input,
            "final_text": run.final_text,
            "tool_calls": [
                {"name": c.name, "input": c.input_parameters, "output": c.output}
                for c in run.tool_calls
            ],
            "errors": run.errors,
            "judge_criteria": metadata.get("judge_criteria", []),
        }
        (run.artifacts / "transcript.json").write_text(
            json.dumps(transcript, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def _final_text(messages: list[BaseMessage]) -> str:
    for msg in reversed(messages):
        if isinstance(msg, AIMessage):
            return msg.text or ""
    return ""


def _tool_calls(messages: list[BaseMessage]) -> list[ToolCall]:
    """Pair each requested tool call with the output of its ToolMessage, in call order."""
    outputs = {m.tool_call_id: m.text for m in messages if isinstance(m, ToolMessage)}
    return [
        ToolCall(
            name=call["name"],
            input_parameters=call["args"],
            output=outputs.get(call["id"] or ""),
        )
        for msg in messages
        if isinstance(msg, AIMessage)
        for call in msg.tool_calls
    ]
