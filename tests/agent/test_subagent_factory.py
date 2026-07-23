"""
Тесты холдера фабрики субагента.

Холдер — мутабельный модульный синглтон (как `_clients`): autouse-fixture сбрасывает
`_factory` до и после каждого теста, чтобы он не протекал между тестами.

Сети/LLM нет: `build()` строит граф create_agent оффлайн-безопасно (build_chat_model лишь
конструирует ChatOpenAI без запроса). Проверяем сборку `AgentConfig` и вычитание `main_only`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.tools import ToolException

import core.agent.tools._subagents as holder
from core.agent.tools import agent_tool
from core.agent.tools._subagents import (
    SubagentFactory,
    from_config,
    get_subagent_factory,
    set_subagent_factory,
)
from core.config import get_config


@pytest.fixture(autouse=True)
def reset_holder():
    """Изолирует процессный синглтон: сброс до и после каждого теста."""
    holder._factory = None
    yield
    holder._factory = None


# --- тулы-маркеры для проверки вычитания main_only ----------------------------


@agent_tool(is_write=False)
async def _plain_tool() -> str:
    """видим всем"""
    return "ok"


@agent_tool(is_write=False, main_only=True)
async def _main_only_tool() -> str:
    """только MainAgent"""
    return "ok"


def _factory_with_agent_md(tmp_path: Path, *, subagent_type="counter-specialist", body="# sub prompt"):
    proc = tmp_path / "procedures"
    (proc / "agents" / subagent_type).mkdir(parents=True)
    (proc / "agents" / subagent_type / "agent.md").write_text(
        f"---\nname: {subagent_type}\ndescription: x\n---\n{body}\n", encoding="utf-8"
    )
    return SubagentFactory(
        config=get_config(),
        full_pool=[_plain_tool, _main_only_tool],
        procedures_root=proc,
    )


# --- get/set ------------------------------------------------------------------


def test_get_before_set_raises_toolexception():
    with pytest.raises(ToolException):
        get_subagent_factory()


def test_error_message_points_to_set():
    with pytest.raises(ToolException, match="set_subagent_factory"):
        get_subagent_factory()


def test_set_then_get_returns_same():
    fake = SubagentFactory(
        config=object(),  # type: ignore[arg-type]
        full_pool=[],
        procedures_root=Path("."),
    )
    set_subagent_factory(fake)
    assert get_subagent_factory() is fake


def test_reset_fixture_isolates():
    with pytest.raises(ToolException):
        get_subagent_factory()


# --- resolve_agent_md ---------------------------------------------------------


def test_resolve_agent_md_existing_and_missing(tmp_path):
    factory = _factory_with_agent_md(tmp_path)
    assert factory.resolve_agent_md("counter-specialist").is_file()
    assert not factory.resolve_agent_md("nope").is_file()


# --- build --------------------------------------------------------------------


def test_build_assembles_subagent_config(tmp_path):
    factory = _factory_with_agent_md(tmp_path)
    ws = tmp_path / "session" / "workspace"
    zone = tmp_path / "session" / "subagents" / "abcdef1234567890"

    sub = factory.build(
        subagent_type="counter-specialist", uid="abcdef1234567890", workspace_path=ws, zone=zone
    )
    cfg = sub.agent_config

    assert cfg.is_subagent is True
    assert cfg.agent_scope == "subagent"
    assert cfg.namespace == "subagent.counter-specialist.abcdef"  # uid[:6]
    assert cfg.workspace_path == ws  # общий workspace для RO-вида
    assert cfg.subagent_path == zone  # зона — аргументом, фабрика путей не хранит
    assert cfg.checkpoints_root is None  # зона субагента не чекпойнтится


def test_build_strips_frontmatter(tmp_path):
    factory = _factory_with_agent_md(tmp_path, body="# sub prompt")
    sub = factory.build(
        subagent_type="counter-specialist", uid="abcdef", workspace_path=tmp_path,
        zone=tmp_path / "zone",
    )
    assert sub.agent_config.agent_md == "# sub prompt"


def test_build_subtracts_main_only_from_pool(tmp_path):
    factory = _factory_with_agent_md(tmp_path)
    sub = factory.build(
        subagent_type="counter-specialist", uid="abcdef", workspace_path=tmp_path,
        zone=tmp_path / "zone",
    )
    names = {t.name for t in sub.agent_config.tool_pool}
    assert "_plain_tool" in names
    assert "_main_only_tool" not in names  # main_only вычтен у субагента


def test_build_unknown_type_raises_filenotfound(tmp_path):
    """Нет agent.md → FileNotFoundError (обработчик конвертирует в DelegationError)."""
    factory = _factory_with_agent_md(tmp_path)
    with pytest.raises(FileNotFoundError):
        factory.build(
            subagent_type="nope", uid="abcdef", workspace_path=tmp_path,
            zone=tmp_path / "zone",
        )


# --- from_config --------------------------------------------------------------


def test_from_config_wires_parts(tmp_path):
    cfg = get_config()
    procedures_root = tmp_path / "procedures"
    factory = from_config(cfg, [_plain_tool], procedures_root)

    assert factory.config is cfg
    assert factory.full_pool == [_plain_tool]
    # procedures_root — общая материализованная папка ПРОЦЕССА (наполняется входом,
    # не from_config); main и субагенты собираются из неё ОДИНАКОВО. Сессионных путей
    # в фабрике нет.
    assert factory.procedures_root == procedures_root


def test_from_config_result_usable_via_holder(tmp_path):
    factory = from_config(get_config(), [], tmp_path / "procedures")
    set_subagent_factory(factory)
    assert get_subagent_factory() is factory
