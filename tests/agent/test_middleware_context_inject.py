"""
Unit-тесты `ContextInjectMiddleware` и `FileContextProvider` в изоляции.

Делим на два пласта:
- провайдер — читает notes/decisions.md с ФС (на temp-каталоге);
- middleware — сборка хвостового блока `<working_context>` (динамика идёт эфемерным
  HumanMessage последним в списке, system_message НЕ трогается — ради prefix-кэша vLLM) +
  гейт субагента, на ФЕЙКОВЫХ провайдерах (без ФС), чтобы проверять именно сборку.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from core.agent.middleware.context_inject import (
    ContextInjectMiddleware,
    FileContextProvider,
)


# --- фейки общего назначения --------------------------------------------------------


@dataclass
class _FakeCtx:
    agent_scope: str = "main"
    workspace_path: Path = field(default_factory=lambda: Path("."))


@dataclass
class _FakeRuntime:
    context: _FakeCtx


class _FakeModelRequest:
    def __init__(self, system_message, ctx: _FakeCtx, messages: list | None = None) -> None:
        self.system_message = system_message
        self.messages = messages if messages is not None else []
        self.runtime = _FakeRuntime(ctx)

    def override(self, *, system_message=..., messages=...):
        return _FakeModelRequest(
            self.system_message if system_message is ... else system_message,
            self.runtime.context,
            self.messages if messages is ... else messages,
        )


class _FakeProvider:
    """Провайдер с заранее заданным блоком (или None)."""

    def __init__(self, tag: str, text: str | None) -> None:
        self.tag = tag
        self._text = text

    async def block(self, ctx) -> str | None:
        return self._text


def _make_handler(return_value="RESP"):
    captured: dict = {}

    async def handler(request):
        captured["request"] = request
        return return_value

    return handler, captured


# --- FileContextProvider: чтение ФС -------------------------------------------------


async def test_file_provider_reads_decisions(tmp_path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "decisions.md").write_text("решили использовать X", encoding="utf-8")

    text = await FileContextProvider().block(_FakeCtx(workspace_path=tmp_path))

    assert text == "решили использовать X"


async def test_file_provider_missing_file_returns_none(tmp_path) -> None:
    text = await FileContextProvider().block(_FakeCtx(workspace_path=tmp_path))
    assert text is None


async def test_file_provider_empty_file_returns_none(tmp_path) -> None:
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "decisions.md").write_text("   \n\n  ", encoding="utf-8")

    text = await FileContextProvider().block(_FakeCtx(workspace_path=tmp_path))

    assert text is None


def test_default_providers_are_file_context_only() -> None:
    mw = ContextInjectMiddleware()
    assert [type(p) for p in mw.providers] == [FileContextProvider]


# --- ContextInjectMiddleware: сборка хвостового блока ------------------------


async def test_main_appends_working_context_as_last_human_message() -> None:
    """Блоки провайдеров уезжают конвертом <working_context> строго последним сообщением."""
    mw = ContextInjectMiddleware(
        providers=[_FakeProvider("block_a", "alpha"), _FakeProvider("block_b", "beta")]
    )
    handler, captured = _make_handler()
    history = [HumanMessage(content="сделай X"), AIMessage(content="думаю")]
    request = _FakeModelRequest(
        SystemMessage(content="BASE"), _FakeCtx(agent_scope="main"), messages=list(history)
    )

    await mw.awrap_model_call(request, handler)

    sent = captured["request"]
    assert sent.messages[:-1] == history           # история нетронута, блок строго последним
    tail = sent.messages[-1]
    assert isinstance(tail, HumanMessage)
    assert tail.content.startswith("<working_context>")
    assert tail.content.rstrip().endswith("</working_context>")
    assert "<block_a>\nalpha\n</block_a>" in tail.content
    assert "<block_b>\nbeta\n</block_b>" in tail.content


async def test_system_message_never_touched() -> None:
    """system обязан быть стабильным между model-call'ами — ради prefix-кэша vLLM.

    Регресс-страж от возврата к старой схеме «инжект в system_message»: провайдерский
    контент не должен просачиваться в системный промпт ни при каких блоках.
    """
    mw = ContextInjectMiddleware(providers=[_FakeProvider("x", "DYNAMIC-DATA")])
    handler, captured = _make_handler()
    base = SystemMessage(content="BASE")
    request = _FakeModelRequest(base, _FakeCtx(agent_scope="main"))

    await mw.awrap_model_call(request, handler)

    assert captured["request"].system_message is base
    assert "DYNAMIC-DATA" not in captured["request"].system_message.content


async def test_subagent_passes_through_untouched() -> None:
    """Субагент: гейт срабатывает раньше провайдеров, запрос не трогаем."""
    mw = ContextInjectMiddleware(providers=[_FakeProvider("X", "data")])
    handler, captured = _make_handler()
    request = _FakeModelRequest(
        SystemMessage(content="BASE"), _FakeCtx(agent_scope="subagent"),
        messages=[HumanMessage(content="задача")],
    )

    await mw.awrap_model_call(request, handler)

    assert captured["request"] is request          # override не вызывался
    assert len(captured["request"].messages) == 1


async def test_no_blocks_leaves_request_untouched() -> None:
    """Все провайдеры вернули None → ни messages, ни system не меняются."""
    mw = ContextInjectMiddleware(providers=[_FakeProvider("X", None)])
    handler, captured = _make_handler()
    request = _FakeModelRequest(
        SystemMessage(content="BASE"), _FakeCtx(agent_scope="main"),
        messages=[HumanMessage(content="привет")],
    )

    await mw.awrap_model_call(request, handler)

    assert captured["request"] is request
    assert len(captured["request"].messages) == 1


async def test_injection_does_not_mutate_original_messages() -> None:
    """Эфемерность: блок собирается в НОВЫЙ список — исходный (история агента) цел.

    Иначе конверт утёк бы в self._messages/messages.jsonl и персистился с историей.
    """
    mw = ContextInjectMiddleware(providers=[_FakeProvider("x", "data")])
    handler, captured = _make_handler()
    original = [HumanMessage(content="сделай X")]
    request = _FakeModelRequest(
        SystemMessage(content="B"), _FakeCtx(agent_scope="main"), messages=original
    )

    await mw.awrap_model_call(request, handler)

    assert len(original) == 1                      # исходный список не мутирован
    assert len(captured["request"].messages) == 2
    assert captured["request"].messages[0] is original[0]
