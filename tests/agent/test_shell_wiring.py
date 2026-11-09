"""Shell ownership and mode propagation at agent construction/runtime boundaries."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent import base, bootstrap
from core.agent.shell.models import ShellArgs, ShellError
from core.agent.shell.shell_manager import ShellManager
from core.agent.tools import _subagents
from core.config import get_config


def capture_config(_infra, config):
    return SimpleNamespace(agent_config=config)


async def test_bootstrap_creates_isolated_session_shell_managers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procedures = tmp_path / "procedures"
    procedures.mkdir()
    (procedures / bootstrap._MAIN_PROMPT_REL).write_text("# main", encoding="utf-8")
    monkeypatch.setattr(bootstrap, "BaseAgent", capture_config)
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first = bootstrap.build_session_agent(first_root, procedures).agent_config.shell_manager
    second = bootstrap.build_session_agent(second_root, procedures).agent_config.shell_manager
    assert isinstance(first, ShellManager)
    assert isinstance(second, ShellManager)
    assert first is not second
    try:
        request = first.prepare_run(
            ShellArgs.model_validate({"action": "run", "command": "echo first"}),
            default_cwd=first_root / "workspace",
            creator="main",
        )
        result = await first.run(request)
        assert result.output is not None and result.output.strip() == "first"
        assert (first_root / "shell_tasks" / f"{result.task_id}.log").is_file()
        assert not (second_root / "shell_tasks").exists()
        with pytest.raises(ShellError):
            second.status(result.task_id)
    finally:
        await first.close()
        await second.close()


@pytest.mark.parametrize("first_mode", ["confirm", "accept_all"])
async def test_runtime_passes_shared_manager_and_current_decision_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_mode: str,
) -> None:
    class Graph:
        context = None

        async def astream(self, _payload, **kwargs):
            self.context = kwargs["context"]
            yield "custom", {"probe": True}

    graph = Graph()
    monkeypatch.setattr(base, "create_agent", lambda **_kwargs: graph)
    monkeypatch.setattr(base, "build_chat_model", lambda _config: object())
    manager = ShellManager(tmp_path / "session")
    config = base.AgentConfig(
        agent_md="# test",
        tool_pool=[],
        namespace="main",
        workspace_path=tmp_path,
        agent_scope="main",
        shell_manager=manager,
    )
    agent = base.BaseAgent(get_config(), config)
    second_mode = "accept_all" if first_mode == "confirm" else "confirm"
    try:
        for decision in (first_mode, second_mode):
            async for _event in agent.run_stream(
                "test",
                permission_mode="act",
                decision_mode=decision,
                session_id="session",
            ):
                pass
            assert graph.context.shell_manager is manager
            assert graph.context.decision_mode == decision
    finally:
        await manager.close()


async def test_subagent_factory_reuses_injected_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procedures = tmp_path / "procedures"
    agent_md = procedures / "agents" / "worker" / "agent.md"
    agent_md.parent.mkdir(parents=True)
    agent_md.write_text("# worker", encoding="utf-8")
    monkeypatch.setattr(_subagents, "BaseAgent", capture_config)
    factory = _subagents.SubagentFactory(get_config(), [], procedures)
    manager = ShellManager(tmp_path / "session")
    try:
        sub = factory.build(
            subagent_type="worker",
            uid="abc123",
            workspace_path=tmp_path / "workspace",
            zone=tmp_path / "session" / "subagents" / "abc123",
            shell_manager=manager,
        )
        assert sub.agent_config.shell_manager is manager
        request = manager.prepare_run(
            ShellArgs.model_validate({"action": "run", "command": "echo shared"}),
            default_cwd=tmp_path,
            creator=sub.agent_config.namespace,
        )
        result = await sub.agent_config.shell_manager.run(request)
        assert manager.status(result.task_id).creator == "subagent.worker.abc123"
        assert result.output is not None and result.output.strip() == "shared"
    finally:
        await manager.close()
