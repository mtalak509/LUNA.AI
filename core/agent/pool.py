"""
Плоский пул тулов MainAgent — единый источник `FULL_POOL` для прод и дев.
"""

from __future__ import annotations

from core.agent.tools.files_tools import (
    read_file, write_file, edit_file, list_files, search_files, delete_file,
)
from core.agent.tools.json_tools import json_inspect, json_search, json_patch
from core.agent.tools.rag_tools import (
    knowledge_base_search,
    knowledge_base_get_reference,
)
from core.agent.tools.delegation_tools import delegate_to_subagent
from core.agent.tools.hitl_tools import (
    ask_user,
    select_from_options,
)


FULL_POOL = [
    read_file,
    write_file,
    edit_file,
    list_files,
    search_files,
    delete_file,
    json_inspect,
    json_search,
    json_patch,
    knowledge_base_search,
    knowledge_base_get_reference,
    delegate_to_subagent,
    ask_user,
    select_from_options,
]
