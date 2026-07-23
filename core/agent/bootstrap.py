"""
Сборка процесса и сессий MainAgent — общий вход для прод (`server.py`) и дев (REPL).

Разрез 15.1: `init_process` — ОДИН раз на процесс (материализация процедурной памяти +
процессные холдеры `set_clients`/`set_subagent_factory`/`set_hitl_registry`);
`build_session_agent` — per-session сборка `BaseAgent` под `/session/<id>/`.
`build_agent` — DEPRECATED шим односессионного режима (все входы переведены в 15.4/15.5).
"""

from __future__ import annotations

import shutil
import warnings
from pathlib import Path
from typing import Callable

from core.config import get_config
from core.agent.base import AgentConfig, BaseAgent, load_agent_prompt
from core.agent.pool import FULL_POOL
from core.agent.tools import assemble_pool
from core.agent.tools._clients import from_config, set_clients
from core.agent.tools._subagents import (
    from_config as subagent_factory_from_config,
    set_subagent_factory,
)
from core.agent.tools._hitl import HitlRegistry, set_hitl_registry


# Процедурная память репозитория — источник для дев-материализации (промпты main + субагентов).
_REPO_PROCEDURES = Path(__file__).parents[1] / "procedures"
# Имя промпта MainAgent внутри procedures_root (субагенты — agents/<type>/agent.md).
_MAIN_PROMPT_REL = "main_agent.ru.md"


def _materialize_procedures(root: Path) -> Path:
    """Скопировать процедурную память репо в root/procedures (дев-шим).

    Делает процедурную память материализованным артефактом ПРОЦЕССА (15.1).
    Перезаписываем на каждый старт (dirs_exist_ok), чтобы правки
    промптов в репо подхватывались при перезапуске. Возвращает procedures_root, из которого
    затем одинаково собираются и MainAgent любой сессии, и субагенты. Имя `procedures`
    в корне сессий зарезервировано (коллизии с session_id нет — тот uuid4.hex).
    """
    procedures_root = root / "procedures"
    shutil.copytree(_REPO_PROCEDURES, procedures_root, dirs_exist_ok=True)
    return procedures_root


def init_process(root: Path) -> Path:
    """Процессная инициализация: процедуры + холдеры. Зовётся ОДИН раз на процесс.

    Порядок: материализация процедур → холдеры (клиенты / фабрика субагента / реестр HITL).
    Холдеры процессные (набор на контейнер) и сессионного состояния не содержат (15.2).
    Возвращает procedures_root — общий для всех сессий процесса.
    """
    cfg = get_config()
    procedures_root = _materialize_procedures(root)
    set_clients(from_config(cfg))
    set_subagent_factory(subagent_factory_from_config(cfg, FULL_POOL, procedures_root))
    set_hitl_registry(HitlRegistry())
    return procedures_root


def build_session_agent(session_root: Path, procedures_root: Path) -> BaseAgent:
    """Собрать MainAgent под корень ОДНОЙ сессии (`/session/<id>/`).

    Промпт main грузится из общего procedures_root процесса; workspace/checkpoints/
    subagents — под session_root. Чекпоинты живут ВНУТРИ сессии — умирают вместе с ней
    при DELETE (желаемое поведение ручной чистки, NB плана 15.1).
    """
    cfg = get_config()
    workspace_path = session_root / "workspace"
    (workspace_path / "notes").mkdir(parents=True, exist_ok=True)
    agent_config = AgentConfig(
        agent_md=load_agent_prompt(procedures_root, _MAIN_PROMPT_REL),
        tool_pool=assemble_pool(
            FULL_POOL, agent_scope="main", disabled_features=cfg.disabled_features
        ),
        namespace="main",
        workspace_path=workspace_path,
        agent_scope="main",
        is_subagent=False,
        checkpoints_root=session_root / "checkpoints",
        subagent_path=None,
    )
    return BaseAgent(cfg, agent_config)

def build_agent_factory(procedures_root: Path) -> dict[str, Callable[[Path], BaseAgent]]:
    """Реестр фабрик агентов по профилям.

    Собирает фабрики агентов по профилю (Состав middleware, tools, начальное состояние workspace)
    """
    def _build(session_root: Path) -> BaseAgent:
        return build_session_agent(session_root, procedures_root)
    
    return {'standalone': _build}

def build_agent(session_root: Path) -> BaseAgent:
    """DEPRECATED: используйте `init_process()` + `build_session_agent()`.

    Переходный шим односессионного режима: корень процесса = корень единственной сессии,
    init + build одним вызовом. Все входы переведены (прод — server.py/SessionManager в 15.4,
    дев — REPL/devfront в 15.5); оставлен только для обратной совместимости внешних скриптов.
    """
    warnings.warn(
        "build_agent() устарел: используйте init_process() + build_session_agent()",
        DeprecationWarning,
        stacklevel=2,
    )
    procedures_root = init_process(session_root)
    return build_session_agent(session_root, procedures_root)
