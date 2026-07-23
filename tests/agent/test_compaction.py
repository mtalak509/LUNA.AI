"""
Unit-тесты `HistoryCompactor` (2.3, своя компакция) — без сети.

Summarizer фейковый (инжектируется), `MessageStore` настоящий на temp-каталоге. Пороги
маленькие, чтобы компакция срабатывала детерминированно. Проверяем: no-op ниже порога;
сжатие выше порога + персист с верным `head` (reload даёт summary + tail); безопасную
границу хвоста (пара AI↔Tool не рвётся).
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from core.agent.compaction import HistoryCompactor
from core.agent.messages import MessageStore


def _fake_summarizer(text: str = "СВОДКА"):
    calls = {"n": 0}

    async def summarize(messages):
        calls["n"] += 1
        return f"{text} из {len(messages)} сообщений"

    return summarize, calls


# --- ниже порога: no-op -------------------------------------------------------------


async def test_no_op_below_threshold(tmp_path) -> None:
    store = MessageStore(tmp_path)
    summarizer, calls = _fake_summarizer()
    # порог недостижимо большой → компакция не должна срабатывать
    comp = HistoryCompactor(store, summarizer, trigger_tokens=10**9, keep_messages=2)

    msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
    result = await comp.maybe_compact(msgs)

    assert result is msgs                 # та же ссылка — историю не трогали
    assert calls["n"] == 0                # summarizer не звали
    assert MessageStore(tmp_path).load() == []   # в журнал ничего не ушло


# --- выше порога: сжатие + персист --------------------------------------------------


async def test_compacts_and_persists_with_correct_head(tmp_path) -> None:
    store = MessageStore(tmp_path)
    msgs = [
        HumanMessage(content="u1"), AIMessage(content="a1"),
        HumanMessage(content="u2"), AIMessage(content="a2"),
        HumanMessage(content="u3"), AIMessage(content="a3"),
    ]
    store.append(msgs)                    # лог: 6 message-записей, message_count=6

    summarizer, calls = _fake_summarizer()
    comp = HistoryCompactor(store, summarizer, trigger_tokens=1, keep_messages=2)

    result = await comp.maybe_compact(msgs)

    # keep=2 → хвост = последние 2; саммари по первым 4
    assert calls["n"] == 1
    assert isinstance(result[0], HumanMessage)
    assert result[0].content.startswith("[Сводка предыдущего диалога]")
    assert "из 4 сообщений" in result[0].content
    assert [m.content for m in result[1:]] == ["u3", "a3"]

    # reload из журнала: маркер-саммари + raw[head:] = summary + [u3, a3]
    reloaded = MessageStore(tmp_path).load()
    assert reloaded[0].content.startswith("[Сводка предыдущего диалога]")
    assert [m.content for m in reloaded[1:]] == ["u3", "a3"]


# --- безопасная граница: пара AI↔Tool не рвётся -------------------------------------


async def test_split_keeps_tool_pair_together(tmp_path) -> None:
    store = MessageStore(tmp_path)
    ai_with_tc = AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": "c1"}])
    tool_msg = ToolMessage(content="result", tool_call_id="c1")
    msgs = [HumanMessage(content="u1"), AIMessage(content="a1"), ai_with_tc, tool_msg]
    store.append(msgs)

    summarizer, _ = _fake_summarizer()
    # keep=1 → наивная граница = индекс 3 (ToolMessage); должна сдвинуться к 2 (AIMessage с tool_calls)
    comp = HistoryCompactor(store, summarizer, trigger_tokens=1, keep_messages=1)

    result = await comp.maybe_compact(msgs)

    tail = result[1:]
    assert tail[0] is ai_with_tc          # хвост начинается с AIMessage(tool_calls), не с ToolMessage
    assert tail[1] is tool_msg            # его результат рядом — пара цела


async def test_split_zero_means_no_compaction(tmp_path) -> None:
    """keep >= длины истории → сжимать нечего, возвращаем как есть."""
    store = MessageStore(tmp_path)
    msgs = [HumanMessage(content="u1"), AIMessage(content="a1")]
    store.append(msgs)

    summarizer, calls = _fake_summarizer()
    comp = HistoryCompactor(store, summarizer, trigger_tokens=1, keep_messages=10)

    result = await comp.maybe_compact(msgs)

    assert result is msgs
    assert calls["n"] == 0
