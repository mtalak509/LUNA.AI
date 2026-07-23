"""Патч конвертера langchain-openai: провести vLLM reasoning-дельты в стрим."""

from __future__ import annotations

import logging
from typing import Any

import langchain_openai.chat_models.base as _lcoai_base
from langchain_core.messages import AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk

_logger = logging.getLogger(__name__)

_REASONING_TEXT_DELTA = "response.reasoning_text.delta"
_REASONING_TEXT_DONE = "response.reasoning_text.done"


def install_reasoning_stream_patch() -> None:
    """Провести vLLM reasoning-дельты через конвертер langchain-openai.

    `langchain-openai` знает только официальные OpenAI-события
    `response.reasoning_summary_*`; vLLM-события `response.reasoning_text.delta/.done`
    падают в `else` конвертера и выбрасываются — reasoning не доезжает до стрима ни
    дельтами, ни финалом. Конвертер — module-level функция (наследник `ChatOpenAI` её
    не переопределит), поэтому оборачиваем атрибут модуля: call-sites
    `_stream_responses`/`_astream_responses` резолвят имя из globals на каждом вызове.

    Форма блока = нестриминговый эталон (как в готовом `AIMessage` от `ainvoke`):
    `{"type": "reasoning", "content": [{"index", "type": "reasoning_text", "text"}],
    "index"}` — по образцу summary-хендлера конвертера, но с ключом `content`.
    Агрегация чанков (langchain-core мержит блоки по `index`) сама соберёт из дельт
    полный reasoning в финальном сообщении `updates`.

    Инварианты: для потоков без reasoning-событий обёртка прозрачна (всё прочее —
    делегат оригиналу); `_strip_reasoning` не трогаем — reasoning по-прежнему не
    персистится; рассчитано на `output_version="responses/v1"`.
    Установка идемпотентна (повторный вызов — no-op).
    """
    original = _lcoai_base._convert_responses_chunk_to_generation_chunk
    if getattr(original, "_reasoning_text_patch", False):
        return

    def convert(
        chunk: Any,
        current_index: int,
        current_output_index: int,
        current_sub_index: int,
        **kwargs: Any,
    ) -> tuple[int, int, int, ChatGenerationChunk | None]:
        chunk_type = getattr(chunk, "type", None)
        if chunk_type not in (_REASONING_TEXT_DELTA, _REASONING_TEXT_DONE):
            return original(
                chunk, current_index, current_output_index, current_sub_index, **kwargs
            )

        # Зеркало `_advance(output_index)` оригинала (без sub_index, как у summary):
        # смена output item → новый блок content; дельты одного item мержатся по index.
        if current_output_index != chunk.output_index:
            current_index += 1
        current_output_index = chunk.output_index

        # `.done` несёт текст целиком — дельты уже отданы, повтор задвоил бы агрегат;
        # пустой блок лишь помечает границу (как у `response.output_text.done`).
        text = chunk.delta if chunk_type == _REASONING_TEXT_DELTA else ""
        block = {
            "type": "reasoning",
            "content": [
                {
                    "index": getattr(chunk, "content_index", 0),
                    "type": "reasoning_text",
                    "text": text,
                }
            ],
            "index": current_index,
        }
        response_metadata = dict(kwargs.get("metadata") or {})
        response_metadata["model_provider"] = "openai"
        message = AIMessageChunk(content=[block], response_metadata=response_metadata)
        return (
            current_index,
            current_output_index,
            current_sub_index,
            ChatGenerationChunk(message=message),
        )

    convert._reasoning_text_patch = True  # type: ignore[attr-defined]
    _lcoai_base._convert_responses_chunk_to_generation_chunk = convert
    _logger.debug("reasoning stream patch installed")
