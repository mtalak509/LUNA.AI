"""
Холдер фабрики субагента для обработчика делегирования.

«Розетка» для `delegate_to_subagent` в обход графа — прямой аналог `_clients.py`:
обработчику тула нужно собрать субагента (та же `BaseAgent`) из процессной инфры —
`InfraConfig` (построить модель), общий `full_pool` (вычесть под субагента) и корень
`procedures/` (прочитать `agents/<type>/agent.md`). Это процессная инфра «один набор
на контейнер», а НЕ per-turn состояние, поэтому она живёт в module-level холдере,
а не в `AgentRuntimeContext` (как и клиенты в 5.0).

Сессионного состояния в фабрике НЕТ: зона субагента (`.../subagents/<uid>/`)
принадлежит конкретной сессии и передаётся в `build()` аргументом — её выводит
`delegate_to_subagent` из runtime-контекста текущего хода.

"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from langchain_core.tools import BaseTool, ToolException

from core.agent.base import AgentConfig, BaseAgent, load_agent_prompt
from core.agent.tools import assemble_pool
from core.config import InfraConfig


def _agent_md_rel(subagent_type: str) -> str:
    """Относительный путь к промпту субагента в процедурной памяти."""
    return f"agents/{subagent_type}/agent.md"


@dataclass(frozen=True)
class SubagentFactory:
    """Собирает экземпляр субагента (`BaseAgent`) из процессной инфры.

    `build()` живёт в фабрике, а не в туле: тонкий `delegate_to_subagent` зовёт
    `factory.build(...)`, а фабрика инкапсулирует чтение `agent.md` + `assemble_pool` +
    сборку `AgentConfig`. Тестируется подменой холдера на стаб.
    """

    config: InfraConfig
    full_pool: list[BaseTool]   # общий пул; под субагента вычитается assemble_pool(subagent)
    procedures_root: Path       # .../procedures/  → agents/<type>/agent.md

    def resolve_agent_md(self, subagent_type: str) -> Path:
        """Путь к `agent.md` специализации. Существование проверяет вызывающий (6.2)."""
        return self.procedures_root / _agent_md_rel(subagent_type)

    def build(
        self,
        *,
        subagent_type: str,
        uid: str,
        workspace_path: Path,
        zone: Path,
    ) -> BaseAgent:
        """Собрать субагента под `subagent_type`/`uid`.

        `workspace_path` — workspace сессии MainAgent (субагент видит его RO через
        `PathScope`); `zone` — RW-зона субагента (`<session_root>/subagents/<uid>/`),
        передаётся вызывающим — фабрика сессионных путей не хранит (15.2). Пул —
        вычитание `main_only` из общего (`assemble_pool(scope="subagent")`). Отсутствует
        `agent.md` → `FileNotFoundError` (обработчик 6.2 конвертирует в `DelegationError`
        — self-correctable «неизвестный тип»).
        """
        agent_md = load_agent_prompt(self.procedures_root, _agent_md_rel(subagent_type))
        agent_config = AgentConfig(
            agent_md=agent_md,
            tool_pool=assemble_pool(
                self.full_pool,
                agent_scope="subagent",
                disabled_features=self.config.disabled_features,
            ),
            namespace=f"subagent.{subagent_type}.{uid[:6]}",
            workspace_path=workspace_path,
            agent_scope="subagent",
            is_subagent=True,
            checkpoints_root=None,         # зона субагента не чекпойнтится (вне workspace)
            subagent_path=zone,
        )
        return BaseAgent(self.config, agent_config)


_factory: SubagentFactory | None = None


def set_subagent_factory(factory: SubagentFactory) -> None:
    """Установить холдер. Зовётся один раз на старте процесса (lifespan / дев-вход)."""
    global _factory
    _factory = factory


def get_subagent_factory() -> SubagentFactory:
    """Получить холдер. Зовётся обработчиком делегирования.
    При отсутствии — выбрасывает `ToolException` (страхует кривую проводку)."""
    if _factory is None:
        raise ToolException(
            "Фабрика субагентов не инициализирована: "
            "set_subagent_factory(...) не был вызван на старте процесса"
        )
    return _factory


def from_config(
    config: InfraConfig,
    full_pool: list[BaseTool],
    procedures_root: Path,
) -> SubagentFactory:
    """Собрать `SubagentFactory` из готовых частей.

    По образцу `_clients.from_config`: прод-`lifespan` собирает ту же фабрику.
    `procedures_root` — общая материализованная процедурная память ПРОЦЕССА,
    из которой так же грузится промпт MainAgent (`build_session_agent`) — main и
    субагенты собираются из ОДНОЙ папки. Материализацию делает вход: копия репо
    (`init_process`).
    """
    return SubagentFactory(
        config=config,
        full_pool=full_pool,
        procedures_root=procedures_root,
    )
