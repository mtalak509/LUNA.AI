"""CLI rendering of the turn stream: what reaches the scrollback for each kind of event.

The view is driven with real StreamEvents and LangChain messages and records into a
non-terminal console, so there is no Live area and every line lands in the export.
"""

from __future__ import annotations

from io import StringIO

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage
from rich.console import Console

from cli.render import TurnView, edit_diff, summarize_args, summarize_result
from core.agent.base import StreamEvent


def _view() -> tuple[TurnView, Console]:
    out = Console(file=StringIO(), width=100, record=True, color_system=None)
    return TurnView(out), out


def _ev(mode: str, data: object) -> StreamEvent:
    return StreamEvent(namespace="main", mode=mode, data=data)


def _call(name: str, args: dict) -> StreamEvent:
    msg = AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "c1"}])
    return _ev("updates", {"model": {"messages": [msg]}})


def _result(name: str, content: str, status: str = "success") -> StreamEvent:
    msg = ToolMessage(content=content, name=name, tool_call_id="c1", status=status)
    return _ev("updates", {"tools": {"messages": [msg]}})


def test_summarize_args_is_one_short_line():
    line = summarize_args({"path": "report.json", "content": "x" * 500, "replace_all": False})
    assert line.startswith("path='report.json', content='xxx")
    assert len(line) <= 90
    assert "\n" not in summarize_args({"text": "a\nb"})


def test_summarize_result_keeps_first_lines_and_counts_the_rest():
    shown, hidden = summarize_result("\n".join(f"line {i}" for i in range(10)))
    assert shown == ["line 0", "line 1", "line 2"]
    assert hidden == 7
    assert summarize_result("") == (["(пусто)"], 0)


def test_edit_diff_only_for_edit():
    diff = edit_diff("edit", {"old_string": '"price": 39.9', "new_string": '"price": 42'})
    plain = [t.plain for t in diff]
    assert '-"price": 39.9' in plain and '+"price": 42' in plain
    assert edit_diff("write", {"content": "x"}) == []


def test_markdown_answer_is_rendered():
    view, out = _view()
    view.start()
    for chunk in ("Нашла **цену**.\n\n", "| a | b |\n|---|---|\n| 1 | 2 |\n"):
        view.handle(_ev("messages", (AIMessageChunk(content=chunk), {"langgraph_node": "model"})))
    view.stop()
    text = out.export_text()
    assert "Нашла цену." in text  # ** ушли в форматирование
    assert "**" not in text
    assert "a  b" in text.replace("│", " ") and "1  2" in text.replace("│", " ")  # таблица
    assert "|" not in text


def test_tool_call_and_result_are_compact():
    view, out = _view()
    view.handle(_call("read_file", {"path": "report.json"}))
    view.handle(_result("read_file", "\n".join(f"row {i}" for i in range(50))))
    text = out.export_text()
    assert "● read_file(path='report.json')" in text
    assert "⎿  row 0" in text
    assert "row 10" not in text
    assert "… ещё 47 строк" in text


def test_failed_tool_result_is_shown():
    view, out = _view()
    view.handle(_result("edit_file", "Пользователь отклонил выполнение действия.", "error"))
    assert "⎿  Пользователь отклонил" in out.export_text()


def test_edit_call_shows_diff():
    view, out = _view()
    view.handle(_call("edit", {"path": "r.json", "old_string": "a: 1", "new_string": "a: 2"}))
    text = out.export_text()
    assert "-a: 1" in text and "+a: 2" in text
    assert "● edit(path='r.json')" in text  # old/new_string только в diff


def test_subagent_work_is_framed_and_indented():
    view, out = _view()
    ns = "subagent.reader_1"
    view.handle(_ev("custom", {"type": "namespace_open", "namespace": ns}))
    inner_call = AIMessage(
        content="", tool_calls=[{"name": "search_files", "args": {"query": "x"}, "id": "s1"}]
    )
    view.handle(
        _ev(
            "custom",
            {
                "type": "subagent_event",
                "namespace": ns,
                "mode": "messages",
                "data": (AIMessageChunk(content="ищу файл"), {"langgraph_node": "model"}),
            },
        )
    )
    view.handle(
        _ev(
            "custom",
            {
                "type": "subagent_event",
                "namespace": ns,
                "mode": "updates",
                "data": {"model": {"messages": [inner_call]}},
            },
        )
    )
    view.handle(_ev("custom", {"type": "namespace_close", "namespace": ns, "error": None}))
    lines = out.export_text().splitlines()
    assert lines[0] == f"  ┌─ субагент {ns} запущен"
    assert "  │ ищу файл" in lines
    assert "  │ ● search_files(query='x')" in lines
    assert lines[-1] == f"  └─ субагент {ns} завершён"


def test_empty_subagent_event_is_skipped():
    view, out = _view()
    for mode in ("messages", "updates"):
        for data in (None, {}):
            event = {"type": "subagent_event", "namespace": "s", "mode": mode, "data": data}
            view.handle(_ev("custom", event))
    view.stop()
    assert out.export_text() == ""


def test_spinner_advances_between_refreshes():
    view, _ = _view()
    first = str(view._bottom().render(0.0))
    later = str(view._bottom().render(0.5))
    assert first.split()[0] != later.split()[0]


def test_subagent_error_is_reported():
    view, out = _view()
    view.handle(_ev("custom", {"type": "namespace_close", "namespace": "s", "error": "boom"}))
    assert "субагент s завершён — ошибка: boom" in out.export_text()


def test_document_patch_and_error_events():
    view, out = _view()
    view.handle(_ev("custom", {"type": "document_patch", "file": "r.json", "operations": [{}, {}]}))
    view.handle(_ev("error", {"code": "model_unavailable", "error": "Модель недоступна"}))
    view.handle(_ev("custom", {"type": "something_else"}))
    text = out.export_text()
    assert "✎ json_patch r.json: 2 оп." in text
    assert "✗ ошибка: Модель недоступна" in text
    assert "[custom] {'type': 'something_else'}" in text


def test_tool_message_tokens_are_not_echoed():
    view, out = _view()
    view.handle(_ev("messages", (AIMessageChunk(content="raw"), {"langgraph_node": "tools"})))
    view.stop()
    assert out.export_text() == ""
