"""Базовый класс агента с ReAct циклом."""

from __future__ import annotations

import logging
from typing import Any

from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from langchain_openai import ChatOpenAI
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.errors import GraphRecursionError
from langchain.agents.middleware import AgentMiddleware
from openai import APIConnectionError

from core.config import InfraConfig
from core.agent.messages import MessageStore
from core.agent.compaction import HistoryCompactor, Summarizer
from core.agent.middleware import PermissionMiddleware, DecisionMiddleware, ContextInjectMiddleware, ToolErrorMiddleware
from core.agent.patches import install_reasoning_stream_patch


_logger = logging.getLogger(__name__)

DEFAULT_RECURSION_LIMIT = 50


class _NamespaceLogAdapter(logging.LoggerAdapter):
    """Префиксует каждую запись namespace'ом агента.

    Main и субагенты живут в одном процессе и пишут в один лог; формат корневого
    логгера (core/app.py) namespace не содержит — вшиваем его в текст сообщения,
    чтобы строки разных агентов были различимы.
    """

    def process(self, msg: str, kwargs: Any) -> tuple[str, Any]:
        return f"[{self.extra['namespace']}] {msg}", kwargs

_SUMMARY_PROMPT = (
    "Сожми диалог ниже в краткую сводку для продолжения работы: цель сессии, ключевые "
    "решения и их причины, затронутые артефакты/файлы, что осталось сделать. "
    "Ответь только сводкой, без вступлений."
)

@dataclass(frozen=True)
class AgentConfig:
    agent_md: str
    tool_pool: list
    namespace: str
    workspace_path: Path
    agent_scope: str
    is_subagent: bool = False
    response_format: Any = None
    checkpoints_root: Path | None = None
    subagent_path: Path | None = None

@dataclass
class AgentRuntimeContext:
    permission_mode: str
    decision_mode: str
    namespace: str      # из AgentConfig, дублируется в runtime для тулов/mw
    workspace_path: Path
    agent_scope: str
    session_id: str     # скоуп HITL-записей; субагент наследует от хода main
    checkpoints_root: Path | None = None  # читается @with_checkpoint через get_runtime()
    subagent_path: Path | None = None
    pointer: str | None = None      # JSON Pointer раздела документа, приходит с UI


# Патч конвертера langchain-openai (reasoning-дельты vLLM) обязан встать ДО первой
# сборки модели — ставим при импорте, рядом с build_chat_model. Тело — в patches/.
install_reasoning_stream_patch()


def build_chat_model(cfg: InfraConfig) -> ChatOpenAI:
    """Построить Langchain-совместимую ChatOpenAI-модель для агента.

    Провайдер выбирается флагом `LLM_PROVIDER` (`cfg.llm_provider`):

    - `gpustack`  — vLLM через GPUStack на **Responses API** (нативная проводка,
      сюда же завязан reasoning-патч стрима);
    - `openrouter`— публичный OpenAI-совместимый шлюз на **Chat Completions**;
    - `ollama`    — локальный Ollama через его OpenAI-совместимый эндпоинт
      (`OLLAMA_BASE_URL` + `/v1`), тоже **Chat Completions**.

    Все три — OpenAI-совместимы, поэтому строятся одним `ChatOpenAI`; различие
    только в `use_responses_api` и наборе полей провайдера.
    """
    if cfg.llm_provider == "openrouter":
        return ChatOpenAI(
            base_url=cfg.openrouter.base_url,
            api_key=cfg.openrouter.api_key or "EMPTY",
            model=cfg.openrouter.llm_model,
            use_responses_api=False,
            temperature=0,
            timeout=cfg.openrouter.timeout,
            max_retries=cfg.openrouter.retries,
        )

    if cfg.llm_provider == "ollama":
        # У Ollama OpenAI-совместимый слой висит на /v1; base_url в конфиге — без него.
        base_url = cfg.ollama.base_url
        if not base_url.endswith("/v1"):
            base_url = f"{base_url}/v1"
        return ChatOpenAI(
            base_url=base_url,
            api_key=cfg.ollama.api_key or "EMPTY",
            model=cfg.ollama.llm_model,
            use_responses_api=False,
            temperature=0,
            timeout=cfg.ollama.timeout,
            max_retries=cfg.ollama.retries,
        )

    # gpustack (дефолт): Responses API как раньше — поведение не меняется.
    return ChatOpenAI(
        base_url=cfg.gpustack.base_url,
        api_key=cfg.gpustack.api_key or "EMPTY",
        model=cfg.gpustack.llm_model,
        use_responses_api=True,
        output_version="responses/v1",
        temperature=0,
        timeout=cfg.gpustack.timeout,
        max_retries=cfg.gpustack.retries,
    )


def _strip_frontmatter(text: str) -> str:
    """Срезать YAML-frontmatter из agent.md (метаданные модели не нужны в system prompt).

    frontmatter (`---`…`---`) — машинные поля (name/description), в модель не уходят
    (конвенция формата промптов). Без frontmatter — текст возвращается как есть.
    """
    if text.startswith("---"):
        text = text.split("---", 2)[-1]
    return text.strip()


def load_agent_prompt(procedures_root: Path, rel_path: str) -> str:
    """Единый загрузчик промпта агента из процедурной памяти (срез frontmatter).

    Парный к `build_chat_model`: тот строит модель, этот — `system_prompt`; вместе это два
    входа `create_agent`. Один путь сборки для MainAgent (`build_session`) и субагента
    (`SubagentFactory.build`): оба читают `agent.md` из одного материализованного
    `procedures_root` одинаково. `rel_path` — относительный (`main_agent.ru.md` |
    `agents/<type>/agent.md`). Нет файла → `FileNotFoundError` (обработчик делегации
    конвертирует в `DelegationError`).

    Источник материализации `procedures_root` — забота входа, не рантайма: копия репо в
    `procedures/` под корнем процесса.
    """
    return _strip_frontmatter((procedures_root / rel_path).read_text(encoding="utf-8"))

@dataclass
class StreamEvent:
    """Одно событие стрима наружу, помеченное namespace своего агента.
    Обертка над LangGraph Event, добавляющая namespace к astream
    """

    namespace: str
    mode: str   # "messages" | "updates" | "custom" — какой stream_mode породил
    data: Any


def _strip_reasoning(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Убрать reasoning-блоки из `content` перед персистом и подачей в следующий ход.

    Reasoning эфемерен (принцип П3): не часть канона диалога и не нужен модели в
    следующих ходах — Responses API шлёт историю без серверного состояния, размышления
    прошлых витков не восстанавливаются и только жгут контекст. Наружу для UX reasoning
    по-прежнему стримится (mode='messages'/'updates' уже отданы), но в `messages.jsonl`
    и рабочую копию `self._messages` не попадает.

    Сообщения с reasoning копируются (`model_copy`) с очищенным content — остальные поля
    (tool_calls, usage_metadata, id, response_metadata) сохраняются; прочие проходят как есть.
    """
    cleaned: list[BaseMessage] = []
    for msg in messages:
        content = msg.content
        if isinstance(content, list):
            kept = [
                block
                for block in content
                if not (isinstance(block, dict) and block.get("type") == "reasoning")
            ]
            if len(kept) != len(content):
                msg = msg.model_copy(update={"content": kept})
        cleaned.append(msg)
    return cleaned


def _is_model_unavailable_error(exc: Exception) -> bool:
    """Распознать временную недоступность LLM backend по OpenAI-compatible ошибке.
    """
    if isinstance(exc, APIConnectionError):     #Проверка доступности сервера vLLM
        return True
    if getattr(exc, "status_code", None) != 404:    #Проверка доступности модели
        return False
    message = str(exc).lower()
    return (
        "model not found" in message
        or "no running instances available" in message
    )


class BaseAgent:
    """Единая runtime-сущность для MainAgent и субагента. Разница — только в AgentConfig.

      Граф create_agent строится ОДИН раз (долгоживущий). Без checkpointer граф между
      ходами stateless — историю держим сами в self._messages и подаём в astream каждый
      ход (шаг 4). messages.jsonl — durable-зеркало этой копии (только у MainAgent).
      """
    def __init__(self, infra_config: InfraConfig, agent_config: AgentConfig,) -> None:
        self.infra_config = infra_config
        self.agent_config = agent_config

        self._graph = create_agent(
            model=build_chat_model(infra_config),
            tools=agent_config.tool_pool,
            system_prompt=agent_config.agent_md,
            middleware=self._build_middleware(),
            context_schema=AgentRuntimeContext,
            response_format=agent_config.response_format,
        )

        self._log = _NamespaceLogAdapter(_logger, {"namespace": agent_config.namespace})

        self._store = None if agent_config.is_subagent else MessageStore(agent_config.workspace_path)
        self._messages = self._store.load() if self._store else []
        # Компакция истории — только у MainAgent (субагент эфемерен).
        self._compactor = None if self._store is None else HistoryCompactor(
            self._store, self._make_summarizer()
        )

        self._log.info(
            "agent ready: is_subagent=%s tools=%d history_loaded=%d",
            agent_config.is_subagent,
            len(agent_config.tool_pool),
            len(self._messages),
        )

    def reload_history(self) -> None:
        """Ре-гидратировать рабочую копию истории из durable-журнала (`messages.jsonl`).

        Зовётся после отката тома `workspace/` (restore чекпоинта): `CheckpointManager`
        меняет только ФС, включая `.runtime/messages.jsonl`, а живая `self._messages`
        осталась «в будущем». Без ре-гидратации агент продолжил бы ход со stale-историей,
        расходящейся с восстановленными файлами. У субагента журнала нет (`self._store is
        None`) — no-op.
        """
        if self._store is not None:
            self._messages = self._store.load()
            self._log.info("history reloaded from store: msgs=%d", len(self._messages))

    @property
    def final_text(self) -> str:
        """Текст последнего AIMessage рабочей копии — `summary` субагента для обработчика
        делегации.

        Инкапсулирует доступ к `self._messages`, чтобы `delegate_to_subagent` не лез в
        приватную копию. Сканируем с конца до первого AIMessage (финал хода — ответ модели).
        Пустая история / не-AIMessage финал → пустая строка.
        """
        for msg in reversed(self._messages):
            if isinstance(msg, AIMessage):
                return msg.text or ""
        return ""

    def _build_middleware(self) -> list[AgentMiddleware]:
        """Построить middleware-стек для агента."""
        return [ToolErrorMiddleware(),PermissionMiddleware(), DecisionMiddleware(), ContextInjectMiddleware()]

    def _make_summarizer(self) -> Summarizer:
        """Фабрика summarizer для HistoryCompactor: один LLM-вызов → текст сводки."""

        async def summarize(messages: list[BaseMessage]) -> str:
            approx_tokens = count_tokens_approximately(messages)
            self._log.info(
                "summarizer start: msgs=%d ~tokens=%d",
                len(messages),
                approx_tokens,
            )
            model = build_chat_model(self.infra_config)
            rendered = "\n\n".join(f"{m.type}: {m.text}" for m in messages)
            resp = await model.ainvoke(
                [SystemMessage(content=_SUMMARY_PROMPT), HumanMessage(content=rendered)]
            )
            summary = resp.text
            self._log.info(
                "summarizer done: summary_chars=%d",
                len(summary),
            )
            return summary

        return summarize

    async def run_stream(
        self,
        text: str,
        *,
        permission_mode: str,
        decision_mode: str,
        session_id: str,
        pointer: str | None = None,
        recursion_limit: int = DEFAULT_RECURSION_LIMIT,
        ) -> AsyncIterator[StreamEvent]:
        """Один ход агента стримом. Async-генератор событий своего namespace.

        Граф без checkpointer ничего не помнит между ходами → подаём ВСЮ рабочую копию
        self._messages каждый ход. Подлинно новые сообщения (из updates-веток model/tools)
        дописываем в копию и зеркалим в messages.jsonl. Токены (mode='messages') только
        отдаём наружу — это частичные чанки для UX, в историю их не пишем.
        """

        namespace = self.agent_config.namespace

        # 1. Контекст хода, конфигурируется на каждый ход.
        context = AgentRuntimeContext(
            permission_mode=permission_mode,
            decision_mode=decision_mode,
            namespace=namespace,
            workspace_path=self.agent_config.workspace_path,
            agent_scope=self.agent_config.agent_scope,
            session_id=session_id,
            checkpoints_root=self.agent_config.checkpoints_root,
            subagent_path=self.agent_config.subagent_path,
            pointer=pointer,
        )

        # 2. Входное сообщение + рабочая копия + запись в журнал
        incoming: list[BaseMessage] = [HumanMessage(content=text)]
        self._messages.extend(incoming)
        if self._store:
            self._store.append(incoming)

        # 2a. Компакция, если история разрослась (сжать один раз, до подачи в граф).
        if self._compactor is not None:
            before_msgs = len(self._messages)
            self._messages = await self._compactor.maybe_compact(self._messages)
            if len(self._messages) != before_msgs:
                self._log.info(
                    "compacted: %d -> %d msgs, ~tokens=%d, summary persisted",
                    before_msgs,
                    len(self._messages),
                    count_tokens_approximately(self._messages),
                )

        # 3. Запуск графа
        self._log.info(
            "turn start: perm=%s decision=%s msgs=%d ~tokens=%d",
            permission_mode,
            decision_mode,
            len(self._messages),
            count_tokens_approximately(self._messages),
        )
        model_steps = 0      # витки ReAct = число завершений узла model
        tool_calls = 0       # суммарно tool_calls, запрошенных моделью за ход
        start_len = len(self._messages)
        try:
            async for mode, data in self._graph.astream(
                {"messages": self._messages},
                context=context,
                stream_mode=["messages", "updates", "custom"],
                config={"recursion_limit": recursion_limit},
            ):
                yield StreamEvent(
                    namespace=namespace,
                    mode=mode,
                    data=data)

                if mode == "updates":
                    for source, update in data.items():
                        if not (source in ("model", "tools") and update and update.get("messages")):
                            continue

                        # Счётчики + per-step DEBUG (только имена тулов).
                        if source == "model":
                            model_steps += 1
                            for m in update["messages"]:
                                for tc in getattr(m, "tool_calls", None) or []:
                                    tool_calls += 1
                                    self._log.debug("tool dispatched: %s", tc.get("name"))
                        else:  # tools
                            for m in update["messages"]:
                                ok = getattr(m, "status", None) != "error"
                                self._log.debug(
                                    "tool result: %s %s",
                                    getattr(m, "name", "?"),
                                    "ok" if ok else "err",
                                )

                        # Reasoning отдан наружу в yield выше, но в канон не персистится.
                        produced_messages = _strip_reasoning(update["messages"])
                        self._messages.extend(produced_messages)
                        if self._store:
                            self._store.append(produced_messages)

            self._log.info(
                "turn done: new_msgs=%d steps=%d tool_calls=%d",
                len(self._messages) - start_len,
                model_steps,
                tool_calls,
            )

        except GraphRecursionError:
            self._log.error(
                "recursion limit exceeded: limit=%d steps=%d", recursion_limit, model_steps
            )
            yield StreamEvent(
                namespace=namespace,
                mode="error",
                data={"error": "recursion limit exceeded", "limit": recursion_limit},
                )
        except Exception as e:
            if _is_model_unavailable_error(e):
                self._log.warning("model unavailable: %s", e)
                yield StreamEvent(
                    namespace=namespace,
                    mode="error",
                    data={"code": "model_unavailable", "error": "Модель недоступна"},
                )
                return

            # Прочие исключения: логируем с traceback и пробрасываем — контракт наружу
            # (кто потребляет стрим) не меняем, глушить нельзя.
            self._log.exception("turn failed with unexpected error")
            raise