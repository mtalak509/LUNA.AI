"""
Unit-тесты шага 2.5: атрибуты тулов (`agent_tool`) и сборка пула (`assemble_pool`).

Без сети и LLM — тулы синтетические, проверяем metadata и логику вычитания.
"""

from __future__ import annotations

from langchain_core.tools import tool

from core.agent.tools import (
    agent_tool,
    assemble_pool,
    fs_scope_of,
    is_main_only,
    is_write_tool,
)

# --- agent_tool: атрибуты доезжают в metadata ---------------------------------------


def test_agent_tool_bare_form_defaults() -> None:
    """@agent_tool без аргументов → дефолтные атрибуты (read-only, не main-only, без зоны)."""

    @agent_tool
    def read_something(x: str) -> str:
        """Док."""
        return x

    assert read_something.name == "read_something"
    assert read_something.metadata == {
        "is_write": False,
        "main_only": False,
        "fs_scope": None,
        "feature": None,
    }


def test_agent_tool_parametrized() -> None:
    @agent_tool(is_write=True, main_only=True, fs_scope="workspace")
    def write_something(x: str) -> str:
        """Док."""
        return x

    assert is_write_tool(write_something) is True
    assert is_main_only(write_something) is True
    assert fs_scope_of(write_something) == "workspace"


def test_agent_tool_custom_name_and_passthrough_kwargs() -> None:
    """Кастомное имя + проброс kwargs (`description`) в нижележащий `tool`."""

    @agent_tool("renamed", description="кастомное описание", is_write=True)
    def original(x: str) -> str:
        """Док."""
        return x

    assert original.name == "renamed"
    assert original.description == "кастомное описание"
    assert is_write_tool(original) is True

    # тул остаётся вызываемым
    assert original.invoke({"x": "hi"}) == "hi"


# --- ридеры безопасны к сторонним тулам без наших атрибутов --------------------------


def test_readers_default_on_plain_tool() -> None:
    @tool
    def plain(x: str) -> str:
        """Сторонний тул без agent_tool-атрибутов (metadata=None)."""
        return x

    assert is_write_tool(plain) is False
    assert is_main_only(plain) is False
    assert fs_scope_of(plain) is None


# --- assemble_pool: сборка вычитанием -----------------------------------------------


def _make_pool() -> list:
    @agent_tool(fs_scope=None)  # не привязан к ФС: RAG-подобный
    def rag_search(q: str) -> str:
        """Док."""
        return q

    @agent_tool(is_write=True, fs_scope="workspace")  # запись в рабочую зону main
    def tp_patch(p: str) -> str:
        """Док."""
        return p

    @agent_tool(main_only=True)  # только MainAgent (напр. delegate_to_subagent)
    def delegate(t: str) -> str:
        """Док."""
        return t

    @agent_tool(fs_scope="workspace")  # read из workspace-зоны (main-территория)
    def read_workspace(path: str) -> str:
        """Док."""
        return path

    return [rag_search, tp_patch, delegate, read_workspace]


def _names(pool) -> set[str]:
    return {t.name for t in pool}


def test_main_act_sees_everything() -> None:
    """MainAgent в Act: весь пул без вычитаний."""
    pool = _make_pool()
    result = assemble_pool(pool, agent_scope="main", permission_mode="act")
    assert _names(result) == {"rag_search", "tp_patch", "delegate", "read_workspace"}


def test_main_plan_excludes_write() -> None:
    """MainAgent в Plan: write-тулы вычитаются."""
    pool = _make_pool()
    result = assemble_pool(pool, agent_scope="main", permission_mode="plan")
    assert _names(result) == {"rag_search", "delegate", "read_workspace"}
    assert all(not is_write_tool(t) for t in result)


def test_subagent_excludes_main_only_and_foreign_fs_zone() -> None:
    """Субагент: вычитаются main_only И тулы из workspace-зоны (не его зона ФС)."""
    pool = _make_pool()
    result = assemble_pool(pool, agent_scope="subagent", permission_mode="act")
    # delegate (main_only), tp_patch + read_workspace (fs_scope=workspace вне зон субагента)
    assert _names(result) == {"rag_search"}


def test_permission_mode_none_keeps_write() -> None:
    """Без переданного режима write-тулы НЕ вычитаются (сборка вне привязки к режиму)."""
    pool = _make_pool()
    result = assemble_pool(pool, agent_scope="main", permission_mode=None)
    assert "tp_patch" in _names(result)


def test_disabled_feature_subtracted() -> None:
    """Тул отключённой подсистемы вычитается; без `disabled_features` — остаётся в пуле."""

    @agent_tool(feature="rag")
    def rag_tool(q: str) -> str:
        """Док."""
        return q

    @agent_tool
    def core_tool(x: str) -> str:
        """Док."""
        return x

    pool = [rag_tool, core_tool]
    assert _names(assemble_pool(pool, agent_scope="main", disabled_features={"rag"})) == {
        "core_tool"
    }
    # проводка цела: без вычитания оба тула на месте
    assert _names(assemble_pool(pool, agent_scope="main")) == {"rag_tool", "core_tool"}
