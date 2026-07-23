"""Тесты сборки процесса/сессии: init_process, build_session_agent,
DEPRECATED-шим build_agent.

Реальный BaseAgent (граф + ChatOpenAI) в тестах не собираем — monkeypatch'им его фейком
и проверяем ФС-эффекты + собранный AgentConfig. init_process гоняем настоящий: клиенты
строятся конструкторами без сети, холдеры — module-level.
"""

from __future__ import annotations

import pytest

from core.agent import bootstrap
from core.agent.tools import _clients, _hitl, _subagents
from core.agent.tools._clients import get_clients
from core.agent.tools._subagents import get_subagent_factory


class FakeBaseAgent:
    """Подмена BaseAgent: фиксирует, с чем его собрали."""

    def __init__(self, infra_config, agent_config):
        self.infra_config = infra_config
        self.agent_config = agent_config


@pytest.fixture
def holders_reset():
    """Сохранить/восстановить module-level холдеры — init_process их перезаписывает."""
    saved = (_clients._clients, _subagents._factory, _hitl._registry)
    yield
    _clients._clients, _subagents._factory, _hitl._registry = saved


@pytest.fixture
def fake_agent_cls(monkeypatch):
    monkeypatch.setattr(bootstrap, "BaseAgent", FakeBaseAgent)


# --- init_process (15.1) --------------------------------------------------------

def test_init_process_materializes_procedures_and_sets_holders(tmp_path, holders_reset):
    procedures_root = bootstrap.init_process(tmp_path)

    # процедурная память материализована в <root>/procedures, промпт main на месте
    assert procedures_root == tmp_path / "procedures"
    assert (procedures_root / bootstrap._MAIN_PROMPT_REL).is_file()

    # процессные холдеры поставлены (rag=None при выключенном RAG — важна установка холдера)
    assert get_clients() is not None
    assert get_subagent_factory() is not None
    assert _hitl.get_hitl_registry() is not None


def test_init_process_is_rerunnable(tmp_path, holders_reset):
    """Повторный вызов (рестарт дев-входа в тот же корень) не падает и обновляет копию."""
    first = bootstrap.init_process(tmp_path)
    second = bootstrap.init_process(tmp_path)
    assert first == second == tmp_path / "procedures"


# --- build_session_agent (15.1/15.5) ----------------------------------------------

def test_build_session_agent_layout_and_config(tmp_path, holders_reset, fake_agent_cls):
    procedures_root = bootstrap.init_process(tmp_path)
    session_root = tmp_path / "s1"

    agent = bootstrap.build_session_agent(session_root, procedures_root)

    # workspace создан (с notes/), config собран под корень СВОЕЙ сессии
    assert (session_root / "workspace" / "notes").is_dir()
    cfg = agent.agent_config
    assert cfg.workspace_path == session_root / "workspace"
    assert cfg.checkpoints_root == session_root / "checkpoints"
    assert cfg.namespace == "main"
    assert cfg.agent_scope == "main"
    assert cfg.is_subagent is False
    # промпт загружен из ОБЩЕГО procedures_root (не пустой, frontmatter срезан)
    assert cfg.agent_md
    assert not cfg.agent_md.lstrip().startswith("---")


def test_build_session_agent_two_sessions_isolated_layout(tmp_path, holders_reset, fake_agent_cls):
    """Две сессии от одного procedures_root — раздельные workspace/checkpoints."""
    procedures_root = bootstrap.init_process(tmp_path)
    a1 = bootstrap.build_session_agent(tmp_path / "s1", procedures_root)
    a2 = bootstrap.build_session_agent(tmp_path / "s2", procedures_root)
    assert a1.agent_config.workspace_path != a2.agent_config.workspace_path
    assert a1.agent_config.checkpoints_root != a2.agent_config.checkpoints_root


# --- build_agent: DEPRECATED-шим ----------------------------------------------------

def test_build_agent_shim_warns_and_works(tmp_path, holders_reset, fake_agent_cls):
    """Шим жив (обратная совместимость), но кидает DeprecationWarning."""
    with pytest.warns(DeprecationWarning, match="init_process"):
        agent = bootstrap.build_agent(tmp_path)

    # эквивалентен паре: корень процесса = корень единственной сессии
    assert (tmp_path / "procedures" / bootstrap._MAIN_PROMPT_REL).is_file()
    assert agent.agent_config.workspace_path == tmp_path / "workspace"
    assert get_clients() is not None
