"""
Flat tool pool of the MainAgent — the single source of `FULL_POOL` for prod and dev.
"""

from __future__ import annotations

from core.agent.tools.delegation_tools import delegate_to_subagent
from core.agent.tools.files_tools import (
    edit,
    find,
    grep,
    ls,
    read,
    rm,
    write,
)
from core.agent.tools.hitl_tools import (
    ask_user,
    select_from_options,
)
from core.agent.tools.rag_tools import (
    knowledge_base_get_reference,
    knowledge_base_search,
)

FULL_POOL = [
    read,
    write,
    edit,
    ls,
    find,
    grep,
    rm,
    knowledge_base_search,
    knowledge_base_get_reference,
    delegate_to_subagent,
    ask_user,
    select_from_options,
]
