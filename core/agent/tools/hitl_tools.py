"""
  HITL-примитивы: ask_user / select_from_options.

  Тонкие декларации поверх `request_human` (`_hitl.py`): тул упаковывает payload, хелпер
  регистрирует future, эмитит событие и паркуется до ответа извне (`/hitl/respond` / REPL).
  Оба — `is_write=False, main_only=False, fs_scope=None`: HITL инициирует любой агент.

  """

from __future__ import annotations

import logging

from langgraph.runtime import get_runtime
from langchain_core.tools import ToolException

from core.agent.tools import agent_tool
from core.agent.tools._hitl import request_human

_logger = logging.getLogger(__name__)
_MAX_OPTIONS = 10


class HitlToolsError(ToolException):
    """Ошибка HITL-примитивов.

    Обертка над ToolException: ToolErrorMiddleware превратит её в
    ToolMessage(status="error") → self-correction.
    """

@agent_tool(is_write=False, main_only=False, fs_scope=None)
async def ask_user(
    question: str,
    context_for_main: str | None = None,
    allow_main_dismissal: bool = True,
) -> dict:
    """Задать пользователю уточняющий вопрос со свободным ответом и дождаться его.

    Используй, если:
    - для продолжения не хватает информации
    - есть логическая развилка по смыслу
    - есть неоднозначность в полученных результатах

    Args:
        question: Вопрос пользователю, самодостаточный и конкретный.
        context_for_main: Зачем тебе ответ и что ты сделаешь дальше.
        allow_main_dismissal: Можно ли снять вопрос без пользователя. Всегда True.

    Возвращает `{"value": <ответ пользователя>, "source": "user"}`.
    """
    try:
        runtime_context = get_runtime().context
        answer = await request_human(
        kind="ask",
        payload={
            "question": question,
            "context_for_main": context_for_main,
            "allow_main_dismissal": allow_main_dismissal,
        },
        namespace=runtime_context.namespace,
        session_id=runtime_context.session_id,
        workspace_path=runtime_context.workspace_path,
        write_marker=(runtime_context.agent_scope == "main"),
        )
        return {"value": answer, "source": "user"}
    except Exception as e:
        raise HitlToolsError(f"Ошибка ask_user: {e}") from e


@agent_tool(is_write=False, main_only=False, fs_scope=None)
async def select_from_options(
    question: str,
    options: list[dict],
    source: str,
    allow_free_text: bool = True,
    allow_main_dismissal: bool = True,
) -> dict:
    """Предложить пользователю выбор из готовых вариантов и дождаться его решения.

        Позволяет предложить пользователю выбрать один из вариантов, который был получен по результатам работы предыдущего тура.
        **Варианты формируются только из предыдущих результатов работы инструмента**  — не сочиняй их сам.

        Args:
            question: Что выбираем и зачем.
            options: 1..10 вариантов, каждый — объект `{"id": ..., "label": ..., "metadata"?: {...}}`.
                `id` — машинный идентификатор для последующих тулов, `label` — текст для пользователя.
            source: Имя тула-источника вариантов (напр. "knowledge_base_search").
            allow_free_text: Разрешить ответ свободным текстом помимо вариантов.
            allow_main_dismissal: True по умолчанию, всегда форвардится пользователю.

        Возвращает либо: 
        - `{"type": "selected", "id": <id варианта>, "source": ...}`;
        - либо (при `allow_free_text`): `{"type": "free_text", "value": <текст>, "source": ...}`.
        """
    if not options:
        raise HitlToolsError("select_from_options: options пуст — нужен хотя бы один вариант")
    if len(options) > _MAX_OPTIONS:
        raise HitlToolsError(
            f"select_from_options: слишком много вариантов ({len(options)} > {_MAX_OPTIONS});"
            "отфильтруй до наболее релевантных вариантов"
        )
    for option in options:
        if not (isinstance(option, dict) and "id" in option and "label" in option):
            raise HitlToolsError(
                  "select_from_options: каждый вариант — объект с полями 'id' и 'label' "
                  "(опционально 'metadata')"
              )

    runtime_context = get_runtime().context
    _logger.debug("select_from_options: source=%s options=%d", source, len(options))
    response = await request_human(
        kind="select",
        payload={
            "question": question,
            "options": options,
            "source": source,
            "allow_free_text": allow_free_text,
            "allow_main_dismissal": allow_main_dismissal,
        },
        namespace=runtime_context.namespace,
        session_id=runtime_context.session_id,
        workspace_path=runtime_context.workspace_path,
        write_marker=(runtime_context.agent_scope == "main"),
        )
    if not isinstance(response, dict):
        raise HitlToolsError(
                    f"select_from_options: ожидался объект-ответ, получено {type(response).__name__}"
                )
    return {**response, "source": source}