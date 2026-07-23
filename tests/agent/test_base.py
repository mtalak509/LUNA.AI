"""
Unit-тесты структур `AgentConfig`/`AgentRuntimeContext` и `run_stream`.

Без сети и без живого LLM: логика `run_stream` проверяется на подменённом графе
(`_FakeGraph`), который отдаёт заранее заданные стрим-события или бросает
`GraphRecursionError`. Единственный сетевой тест — `test_build_chat_model_single_call` —
помечен `integration` и по умолчанию исключён.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage
from langgraph.errors import GraphRecursionError

from core.agent.base import (
    DEFAULT_RECURSION_LIMIT,
    AgentConfig,
    AgentRuntimeContext,
    BaseAgent,
    StreamEvent,
    _strip_reasoning,
    build_chat_model,
)
from core.agent.messages import MessageStore
from core.config import InfraConfig, get_config


def make_main_config(**overrides) -> AgentConfig:
    """MainAgent-конфиг по умолчанию; отдельные поля переопределяются через overrides."""
    params = dict(
        agent_md="# main agent",
        tool_pool=[],
        namespace="main",
        workspace_path=Path("/session/workspace"),
        agent_scope="main",
    )
    params.update(overrides)
    return AgentConfig(**params)


# --- AgentConfig --------------------------------------------------------------------


def test_agent_config_is_frozen() -> None:
    """frozen=True: атрибут нельзя переназначить после сборки (граф строится по нему один раз)."""
    config = make_main_config()
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.agent_scope = "subagent"  # type: ignore[misc]


def test_agent_config_defaults() -> None:
    config = make_main_config()
    assert config.is_subagent is False
    assert config.response_format is None


def test_agent_config_holds_all_fields() -> None:
    pool = [object()]
    config = make_main_config(
        agent_md="# sub",
        tool_pool=pool,
        namespace="subagent.rag.run1",
        agent_scope="subagent",
        is_subagent=True,
        response_format=object(),  # плейсхолдер ToolStrategy(ResultModel)
    )
    assert config.agent_md == "# sub"
    assert config.tool_pool is pool
    assert config.namespace == "subagent.rag.run1"
    assert config.agent_scope == "subagent"
    assert config.is_subagent is True
    assert config.response_format is not None


# --- AgentRuntimeContext ------------------------------------------------------------


def test_runtime_context_holds_modes_and_static() -> None:
    """Контекст хода несёт динамику (режимы) + продублированную статику для тулов/mw."""
    ctx = AgentRuntimeContext(
        permission_mode="plan",
        decision_mode="confirm",
        namespace="main",
        workspace_path=Path("/session/workspace"),
        agent_scope="main",
        session_id="s1",
    )
    assert ctx.permission_mode == "plan"
    assert ctx.decision_mode == "confirm"
    assert ctx.namespace == "main"
    assert ctx.workspace_path == Path("/session/workspace")
    assert ctx.agent_scope == "main"
    assert ctx.session_id == "s1"


def test_runtime_context_is_mutable() -> None:
    """Не frozen: run_stream (2.2) пересобирает контекст на каждый ход; режим можно сменить."""
    ctx = AgentRuntimeContext(
        permission_mode="plan",
        decision_mode="confirm",
        namespace="main",
        workspace_path=Path("/session/workspace"),
        agent_scope="main",
        session_id="s1",
    )
    ctx.permission_mode = "act"  # не должно падать
    assert ctx.permission_mode == "act"


# --- build_chat_model: живой smoke против GPUStack (Responses API) -------------------
# Помечен `integration`: ходит в сеть на cfg.gpustack.base_url, по умолчанию исключён.
# Запуск вручную:  pytest tests/agent/test_base.py -s -m integration


@pytest.mark.integration
async def test_build_chat_model_single_call() -> None:
    """Один живой вызов: фабрика поднимает рабочую модель, Responses API отвечает,
    reasoning доезжает нативно (content_blocks), msg.text чистый, usage присутствует."""
    model = build_chat_model(get_config())

    msg = await model.ainvoke([HumanMessage("Reply with exactly one word: ping")])

    # Чистый ответ — без портянки размышлений (заслуга output_version="responses/v1").
    print("\ntext   :", repr(msg.text))
    # Reasoning приходит отдельным блоком, не в .text.
    block_types = [b.get("type") for b in (msg.content_blocks or [])]
    print("blocks :", block_types)
    print("usage  :", msg.usage_metadata)

    assert msg.text.strip(), "ожидали непустой текстовый ответ"
    # Серверное состояние Responses API не используется — история на ФС (Ф2).
    assert msg.response_metadata.get("previous_response_id") is None


# --- build_chat_model: выбор провайдера (офлайн, без сети) --------------------------


def test_build_chat_model_gpustack_uses_responses_api() -> None:
    """Дефолтный провайдер — GPUStack на Responses API (поведение не изменилось)."""
    cfg = InfraConfig(LLM_PROVIDER="gpustack")
    model = build_chat_model(cfg)

    assert cfg.llm_provider == "gpustack"
    assert model.use_responses_api is True
    assert model.model_name == cfg.gpustack.llm_model
    assert str(model.openai_api_base) == cfg.gpustack.base_url


def test_build_chat_model_openrouter_chat_completions() -> None:
    """OpenRouter — Chat Completions (не Responses), свой base_url и модель."""
    cfg = InfraConfig(LLM_PROVIDER="openrouter")
    model = build_chat_model(cfg)

    assert model.use_responses_api is False
    assert model.model_name == cfg.openrouter.llm_model
    assert str(model.openai_api_base) == cfg.openrouter.base_url


def test_build_chat_model_ollama_appends_v1_suffix() -> None:
    """Ollama — Chat Completions; /v1 добавляется к base_url автоматически."""
    cfg = InfraConfig(LLM_PROVIDER="ollama")
    model = build_chat_model(cfg)

    assert model.use_responses_api is False
    assert model.model_name == cfg.ollama.llm_model
    assert str(model.openai_api_base).endswith("/v1")


# --- run_stream на подменённом графе (без сети) -------------------------------------


class _FakeGraph:
    """Подмена скомпилированного create_agent-графа.

    `astream` либо отдаёт заранее заданные `(mode, data)` события, либо бросает
    исключение (для проверки перехвата `recursion_limit`). Запоминает аргументы
    последнего вызова, чтобы тест мог проверить, что run_stream подаёт полную историю
    и корректный контекст хода.
    """

    def __init__(self, events=None, raises=None) -> None:
        self._events = events or []
        self._raises = raises
        self.last_payload = None
        self.last_kwargs = None

    async def astream(self, payload, **kwargs):
        self.last_payload = payload
        self.last_kwargs = kwargs
        if self._raises is not None:
            raise self._raises
        for item in self._events:
            yield item


def make_agent(tmp_path, *, is_subagent=False, namespace="main", events=None, raises=None) -> BaseAgent:
    """BaseAgent с рабочей зоной в tmp_path и подменённым на `_FakeGraph` графом.

    __init__ строит настоящий граф (offline-безопасно), затем мы заменяем его фейком —
    тестируем логику run_stream, а не create_agent.
    """
    config = AgentConfig(
        agent_md="# test",
        tool_pool=[],
        namespace=namespace,
        workspace_path=tmp_path,
        agent_scope="subagent" if is_subagent else "main",
        is_subagent=is_subagent,
    )
    agent = BaseAgent(get_config(), config)
    agent._graph = _FakeGraph(events=events, raises=raises)
    return agent


async def _drain(agent: BaseAgent) -> list[StreamEvent]:
    """Прогнать один ход и собрать все события стрима."""
    return [
        ev
        async for ev in agent.run_stream(
            "hi", permission_mode="act", decision_mode="accept_all", session_id="dev"
        )
    ]


def _sample_events() -> list[tuple]:
    """Типовой выхлоп одного хода: токен (messages) + готовое сообщение (updates) + custom."""
    return [
        ("messages", (AIMessageChunk(content="he"), {"langgraph_node": "model"})),
        ("updates", {"model": {"messages": [AIMessage(content="hello")]}}),
        ("custom", {"status": "working"}),
    ]


async def test_run_stream_tags_every_event_with_namespace(tmp_path) -> None:
    """Каждое событие обёрнуто в StreamEvent и помечено namespace своего агента."""
    agent = make_agent(tmp_path, namespace="main", events=_sample_events())

    events = await _drain(agent)

    assert all(isinstance(e, StreamEvent) for e in events)
    assert all(e.namespace == "main" for e in events)
    # Порядок и режимы сохранены как пришли из astream.
    assert [e.mode for e in events] == ["messages", "updates", "custom"]


async def test_run_stream_appends_updates_not_tokens(tmp_path) -> None:
    """В историю попадают только готовые сообщения из updates (model/tools), не токены."""
    agent = make_agent(tmp_path, events=_sample_events())

    await _drain(agent)

    # Рабочая копия: входной Human + произведённый AI; токен и custom НЕ дописаны.
    assert len(agent._messages) == 2
    assert isinstance(agent._messages[0], HumanMessage) and agent._messages[0].content == "hi"
    assert isinstance(agent._messages[1], AIMessage) and agent._messages[1].content == "hello"

    # То же зеркалится в durable-журнал и переживает перезагрузку из jsonl.
    reloaded = MessageStore(tmp_path).load()
    assert [type(m).__name__ for m in reloaded] == ["HumanMessage", "AIMessage"]
    assert reloaded[0].content == "hi"
    assert reloaded[1].content == "hello"


async def test_run_stream_feeds_full_history_and_turn_context(tmp_path) -> None:
    """astream получает всю рабочую копию + свежий контекст хода с режимами и лимитом."""
    agent = make_agent(tmp_path, events=[])  # пустой стрим — важны только аргументы вызова

    await _drain(agent)

    fake = agent._graph
    # Подаётся именно рабочая копия с уже добавленным входным сообщением.
    assert fake.last_payload["messages"] is agent._messages
    assert any(isinstance(m, HumanMessage) and m.content == "hi" for m in fake.last_payload["messages"])

    ctx = fake.last_kwargs["context"]
    assert ctx.permission_mode == "act"
    assert ctx.decision_mode == "accept_all"
    assert ctx.namespace == "main"
    assert fake.last_kwargs["stream_mode"] == ["messages", "updates", "custom"]
    assert fake.last_kwargs["config"]["recursion_limit"] == DEFAULT_RECURSION_LIMIT


async def test_run_stream_catches_recursion_limit(tmp_path) -> None:
    """Превышение recursion_limit не роняет процесс — отдаётся событие mode='error'."""
    agent = make_agent(tmp_path, raises=GraphRecursionError("limit"))

    events = await _drain(agent)  # не должно бросать наружу

    assert len(events) == 1
    assert events[0].mode == "error"
    assert events[0].namespace == "main"
    assert events[0].data["limit"] == DEFAULT_RECURSION_LIMIT


class _OpenAIShapeError(Exception):
    """Минимальная форма openai.APIStatusError для duck-typing в run_stream."""

    status_code = 404


async def test_run_stream_maps_model_unavailable_404_to_error_event(tmp_path) -> None:
    """vLLM/GPUStack 404 на рестарте модели отдаётся UI как стабильная ошибка."""
    exc = _OpenAIShapeError("Model not found or no running instances available")
    agent = make_agent(tmp_path, raises=exc)

    events = await _drain(agent)  # не должно бросать наружу

    assert len(events) == 1
    assert events[0].mode == "error"
    assert events[0].namespace == "main"
    assert events[0].data == {
        "code": "model_unavailable",
        "error": "Модель недоступна",
    }


async def test_run_stream_reraises_unexpected_errors(tmp_path) -> None:
    """Неизвестные ошибки model node остаются fail-fast и логируются с traceback."""
    agent = make_agent(tmp_path, raises=RuntimeError("boom"))

    with pytest.raises(RuntimeError, match="boom"):
        await _drain(agent)


# --- reasoning не персистится в канон ----------------------------------------


def _ai_with_reasoning(text: str = "answer") -> AIMessage:
    """AIMessage в формате responses/v1: reasoning-блок + текстовый блок в content-листе."""
    return AIMessage(
        content=[
            {"type": "reasoning", "content": [{"type": "reasoning_text", "text": "думаю..."}]},
            {"type": "text", "text": text},
        ]
    )


def test_strip_reasoning_removes_only_reasoning_blocks() -> None:
    """Хелпер вырезает reasoning, сохраняет прочие блоки и поля сообщения."""
    msg = _ai_with_reasoning("hi")
    msg.tool_calls.append({"name": "t", "args": {}, "id": "c1"})

    [cleaned] = _strip_reasoning([msg])

    types = [b["type"] for b in cleaned.content]
    assert types == ["text"]                      # reasoning ушёл, text остался
    assert cleaned.tool_calls == msg.tool_calls   # прочие поля сохранены (model_copy)
    assert msg.content[0]["type"] == "reasoning"   # исходное сообщение не мутировали


def test_strip_reasoning_passes_through_plain_messages() -> None:
    """Строковый content и сообщения без reasoning проходят без изменений."""
    plain = AIMessage(content="just text")
    human = HumanMessage(content="hi")
    assert _strip_reasoning([plain, human]) == [plain, human]


async def test_run_stream_does_not_persist_reasoning(tmp_path) -> None:
    """Reasoning из updates не попадает ни в рабочую копию, ни в messages.jsonl."""
    agent = make_agent(
        tmp_path,
        events=[("updates", {"model": {"messages": [_ai_with_reasoning("done")]}})],
    )

    await _drain(agent)

    # Рабочая копия: Human + AI без reasoning-блока.
    ai = agent._messages[1]
    assert [b["type"] for b in ai.content] == ["text"]

    # И в durable-журнале reasoning отсутствует после reload.
    reloaded = MessageStore(tmp_path).load()
    persisted_ai = reloaded[1]
    assert all(b.get("type") != "reasoning" for b in persisted_ai.content)


# --- final_text: summary субагента для обработчика делегации --------------


def test_final_text_returns_last_aimessage_text(tmp_path) -> None:
    """final_text отдаёт текст последнего AIMessage рабочей копии."""
    agent = make_agent(tmp_path)
    agent._messages = [
        HumanMessage(content="hi"),
        AIMessage(content="первый"),
        HumanMessage(content="ещё"),
        AIMessage(content="итог субагента"),
    ]
    assert agent.final_text == "итог субагента"


def test_final_text_skips_trailing_non_ai(tmp_path) -> None:
    """Сканирование с конца находит последний AIMessage даже если финал — не он."""
    agent = make_agent(tmp_path)
    agent._messages = [AIMessage(content="ответ"), HumanMessage(content="реплика")]
    assert agent.final_text == "ответ"


def test_final_text_empty_when_no_aimessage(tmp_path) -> None:
    """Пустая история / нет AIMessage → пустая строка (не падение)."""
    agent = make_agent(tmp_path)
    agent._messages = []
    assert agent.final_text == ""
    agent._messages = [HumanMessage(content="hi")]
    assert agent.final_text == ""


async def test_subagent_has_no_store_but_keeps_in_memory_history(tmp_path) -> None:
    """Субагент эфемерен: durable-журнала нет, но рабочая копия в памяти растёт."""
    agent = make_agent(
        tmp_path,
        is_subagent=True,
        namespace="subagent.rag.run1",
        events=[("updates", {"model": {"messages": [AIMessage(content="done")]}})],
    )

    assert agent._store is None
    assert agent._messages == []  # холодный контекст на старте

    events = await _drain(agent)

    # История в памяти наполнилась, но на диск ничего не ушло.
    assert any(isinstance(m, AIMessage) and m.content == "done" for m in agent._messages)
    assert not (tmp_path / ".runtime" / "messages.jsonl").exists()
    assert all(e.namespace == "subagent.rag.run1" for e in events)
