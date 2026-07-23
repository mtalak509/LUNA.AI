"""
Тесты обёртки конвертера reasoning-дельт.

Юниты — на синтетических SSE-событиях (SimpleNamespace с полями реальных событий
openai SDK): при бампе langchain-openai, сломавшем контракт конвертера, падают громко.
Живой smoke против GPUStack — `@pytest.mark.integration` (исключён по умолчанию;
запуск: pytest tests/agent/test_reasoning_patch.py -s -m integration).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage

import langchain_openai.chat_models.base as lcoai_base

# Импорт core.agent.base устанавливает патч (module-level side effect);
# тело патча — core/agent/patches/langchain_openai_reasoning.py.
from core.agent.base import build_chat_model
from core.agent.patches import install_reasoning_stream_patch
from core.config import get_config


def _convert(chunk, current_index=-1, current_output_index=-1, current_sub_index=-1, **kwargs):
    """Вызов конвертера как в call-site `_astream_responses` (позиционные + kwargs)."""
    return lcoai_base._convert_responses_chunk_to_generation_chunk(
        chunk,
        current_index,
        current_output_index,
        current_sub_index,
        schema=kwargs.pop("schema", None),
        metadata=kwargs.pop("metadata", None),
        has_reasoning=kwargs.pop("has_reasoning", False),
        output_version=kwargs.pop("output_version", "responses/v1"),
    )


def _reasoning_delta(delta: str, *, output_index=0, content_index=0):
    return SimpleNamespace(
        type="response.reasoning_text.delta",
        output_index=output_index,
        content_index=content_index,
        item_id="rs_1",
        delta=delta,
    )


def _reasoning_done(text: str, *, output_index=0, content_index=0):
    return SimpleNamespace(
        type="response.reasoning_text.done",
        output_index=output_index,
        content_index=content_index,
        item_id="rs_1",
        text=text,
    )


def _text_delta(delta: str, *, output_index=1, content_index=0):
    return SimpleNamespace(
        type="response.output_text.delta",
        output_index=output_index,
        content_index=content_index,
        item_id="msg_1",
        delta=delta,
    )


# --- установка патча -------------------------------------------------------------------

def test_patch_installed_on_import():
    """Импорт core.agent.base подменяет module-level конвертер langchain-openai."""
    fn = lcoai_base._convert_responses_chunk_to_generation_chunk
    assert getattr(fn, "_reasoning_text_patch", False) is True


def test_patch_install_idempotent():
    """Повторная установка — no-op, обёртка не наворачивается на саму себя."""
    before = lcoai_base._convert_responses_chunk_to_generation_chunk
    install_reasoning_stream_patch()
    assert lcoai_base._convert_responses_chunk_to_generation_chunk is before


# --- reasoning-события -----------------------------------------------------------------

def test_reasoning_delta_becomes_reasoning_block():
    """`response.reasoning_text.delta` → чанк с reasoning-блоком эталонной формы."""
    idx, out_idx, sub_idx, gen = _convert(_reasoning_delta("думаю..."))

    assert gen is not None
    (block,) = gen.message.content
    assert block == {
        "type": "reasoning",
        "content": [{"index": 0, "type": "reasoning_text", "text": "думаю..."}],
        "index": 0,  # advance: -1 → 0 на новом output item
    }
    assert (idx, out_idx, sub_idx) == (0, 0, -1)
    # UI-паттерн потребления: chunk.content_blocks с фильтром type=="reasoning".
    reasoning = [b for b in gen.message.content_blocks if b.get("type") == "reasoning"]
    assert reasoning and reasoning[0]["content"][0]["text"] == "думаю..."


def test_reasoning_done_is_empty_marker():
    """`.done` не дублирует текст (дельты уже отданы; повтор задвоил бы агрегат)."""
    _, _, _, gen = _convert(_reasoning_done("весь текст целиком"))
    (block,) = gen.message.content
    assert block["content"][0]["text"] == ""


def test_reasoning_deltas_share_index_and_aggregate():
    """Дельты одного output item не двигают index → агрегат склеивает текст."""
    idx, out_idx, sub_idx, gen1 = _convert(_reasoning_delta("foo "))
    idx, out_idx, sub_idx, gen2 = _convert(
        _reasoning_delta("bar"), idx, out_idx, sub_idx
    )
    assert gen1.message.content[0]["index"] == gen2.message.content[0]["index"] == 0

    aggregate: AIMessageChunk = gen1.message + gen2.message
    (block,) = aggregate.content
    assert block["content"][0]["text"] == "foo bar"


def test_new_output_item_advances_index():
    """Смена output_index → новый content-блок (зеркало `_advance` оригинала)."""
    idx, out_idx, sub_idx, gen1 = _convert(_reasoning_delta("a", output_index=0))
    idx, out_idx, sub_idx, gen2 = _convert(
        _text_delta("hello", output_index=1), idx, out_idx, sub_idx
    )
    assert gen1.message.content[0]["index"] == 0
    assert gen2.message.content[0]["index"] == 1


def test_mixed_stream_aggregates_reasoning_and_text():
    """Смешанный поток: reasoning + текст агрегируются в разные блоки, tool-путь цел."""
    state = (-1, -1, -1)
    chunks = []
    for event in [
        _reasoning_delta("шаг 1. "),
        _reasoning_delta("шаг 2."),
        _reasoning_done("шаг 1. шаг 2."),
        _text_delta("Ответ", output_index=1),
    ]:
        *state, gen = _convert(event, *state)
        chunks.append(gen.message)

    aggregate = chunks[0]
    for m in chunks[1:]:
        aggregate = aggregate + m

    reasoning = [b for b in aggregate.content if b.get("type") == "reasoning"]
    texts = [b for b in aggregate.content if b.get("type") == "text"]
    assert reasoning[0]["content"][0]["text"] == "шаг 1. шаг 2."
    assert texts[0]["text"] == "Ответ"


# --- прозрачность для не-reasoning событий ----------------------------------------------

def test_non_reasoning_events_delegate_to_original():
    """Текстовая дельта проходит через оригинальный конвертер как раньше."""
    idx, out_idx, sub_idx, gen = _convert(_text_delta("hi", output_index=0))
    (block,) = gen.message.content
    assert block == {"type": "text", "text": "hi", "index": 0}


def test_unknown_event_still_dropped():
    """Неизвестное событие — как у оригинала: чанк None, индексы не сдвинуты."""
    event = SimpleNamespace(type="response.something_else", output_index=0)
    idx, out_idx, sub_idx, gen = _convert(event)
    assert gen is None
    assert (idx, out_idx, sub_idx) == (-1, -1, -1)


# --- живой smoke (GPUStack) --------------------------------------------------------------

@pytest.mark.integration
async def test_astream_yields_reasoning_deltas() -> None:
    """критерий: `model.astream` отдаёт reasoning-дельты в `content_blocks`,
    итоговый текст ответа не сломан."""
    model = build_chat_model(get_config())

    reasoning_text = ""
    answer_text = ""
    async for chunk in model.astream(
        [HumanMessage("Сколько будет 17*23? Ответь одним числом.")]
    ):
        for block in chunk.content_blocks:
            if block.get("type") == "reasoning":
                for part in block.get("content") or []:
                    reasoning_text += part.get("text") or ""
        answer_text += chunk.text or ""

    assert reasoning_text.strip(), "reasoning-дельты не пришли"
    assert "391" in answer_text
