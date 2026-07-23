"""
Тесты HITL-тулов: ask_user / select_from_options.

`confirm_with_user` как отдельный тул упразднён — подтверждение теперь гейтится
программно в `DecisionMiddleware` (см. test_middleware_decision.py), не завязано на
вызов модели.

Тулы — тонкие декларации поверх `request_human` + `get_runtime().context`. Оба зависимых
имени импортированы в модуль `hitl_tools`, поэтому мокаем их там: `get_runtime` → фейковый
контекст, `request_human` → перехват вызова + подставной ответ. Тул вызываем через
`.ainvoke(args)` — прямой вызов тула возвращает его content (не ToolMessage) и пробрасывает
ToolException наружу (handle_tool_error по умолчанию выключен).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core.agent.tools import hitl_tools
from core.agent.tools.hitl_tools import (
    HitlToolsError,
    ask_user,
    select_from_options,
)


@pytest.fixture
def hitl_env(monkeypatch, tmp_path):
    """Подменяет get_runtime (фейковый ctx) и request_human (перехват + подставной ответ)."""
    ctx = SimpleNamespace(
        namespace="main", workspace_path=tmp_path, agent_scope="main", session_id="s1"
    )
    monkeypatch.setattr(hitl_tools, "get_runtime", lambda: SimpleNamespace(context=ctx))

    captured: dict = {}
    box = {"value": None}

    async def fake_request_human(
        kind, payload, *, namespace, session_id, workspace_path, write_marker
    ):
        captured.update(
            kind=kind, payload=payload, namespace=namespace, session_id=session_id,
            workspace_path=workspace_path, write_marker=write_marker,
        )
        return box["value"]

    monkeypatch.setattr(hitl_tools, "request_human", fake_request_human)
    return SimpleNamespace(ctx=ctx, captured=captured, box=box)


# --- ask_user ----------------------------------------------------------------

async def test_ask_user_wraps_answer_and_forwards_payload(hitl_env):
    hitl_env.box["value"] = "материал 40Х"
    result = await ask_user.ainvoke(
        {"question": "Какой материал?", "context_for_main": "нужно для маршрута"}
    )
    assert result == {"value": "материал 40Х", "source": "user"}

    cap = hitl_env.captured
    assert cap["kind"] == "ask"
    assert cap["payload"]["question"] == "Какой материал?"
    assert cap["payload"]["context_for_main"] == "нужно для маршрута"
    assert cap["namespace"] == "main"
    assert cap["session_id"] == "s1"  # скоуп из runtime-контекста
    assert cap["write_marker"] is True  # agent_scope == "main"


async def test_ask_user_subagent_scope_no_marker(hitl_env):
    hitl_env.ctx.agent_scope = "subagent"
    hitl_env.ctx.namespace = "subagent.default.abc123"
    hitl_env.box["value"] = "ответ"
    await ask_user.ainvoke({"question": "q", "context_for_main": "c"})
    assert hitl_env.captured["write_marker"] is False
    assert hitl_env.captured["namespace"] == "subagent.default.abc123"


# --- select_from_options -----------------------------------------------------

async def test_select_selected_passthrough_with_source(hitl_env):
    hitl_env.box["value"] = {"type": "selected", "id": "opt2"}
    result = await select_from_options.ainvoke({
        "question": "Выбери класс",
        "options": [{"id": "opt1", "label": "A"}, {"id": "opt2", "label": "B"}],
        "source": "knowledge_base_search",
    })
    assert result == {"type": "selected", "id": "opt2", "source": "knowledge_base_search"}
    assert hitl_env.captured["kind"] == "select"
    assert hitl_env.captured["payload"]["source"] == "knowledge_base_search"


async def test_select_free_text_passthrough(hitl_env):
    hitl_env.box["value"] = {"type": "free_text", "value": "свой вариант"}
    result = await select_from_options.ainvoke({
        "question": "q",
        "options": [{"id": "1", "label": "A"}],
        "source": "src",
    })
    assert result == {"type": "free_text", "value": "свой вариант", "source": "src"}


async def test_select_empty_options_rejected_before_park(hitl_env):
    with pytest.raises(HitlToolsError):
        await select_from_options.ainvoke({"question": "q", "options": [], "source": "s"})
    assert hitl_env.captured == {}  # request_human не вызывался


async def test_select_too_many_options_rejected(hitl_env):
    opts = [{"id": str(i), "label": str(i)} for i in range(11)]
    with pytest.raises(HitlToolsError):
        await select_from_options.ainvoke({"question": "q", "options": opts, "source": "s"})
    assert hitl_env.captured == {}


async def test_select_option_missing_fields_rejected(hitl_env):
    with pytest.raises(HitlToolsError):
        await select_from_options.ainvoke({
            "question": "q",
            "options": [{"id": "1"}],  # нет label
            "source": "s",
        })
    assert hitl_env.captured == {}


async def test_select_non_dict_response_rejected(hitl_env):
    hitl_env.box["value"] = "не объект"
    with pytest.raises(HitlToolsError):
        await select_from_options.ainvoke({
            "question": "q",
            "options": [{"id": "1", "label": "A"}],
            "source": "s",
        })
