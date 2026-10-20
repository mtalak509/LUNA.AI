"""
Unit tests for `MessageStore` (`messages.jsonl`).

No network or LLM — on a temporary directory. Covers: load/append round-trip, tolerance of an
empty/missing file, append-only, reconstruction from the summary marker (`head`), restoring
`message_count`, moving heavy base64 into a blob (a reference instead of inline data).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from core.agent.messages import MessageStore


def _jsonl(workspace: Path) -> Path:
    return workspace / ".runtime" / "messages.jsonl"


# --- пустой / отсутствующий файл ----------------------------------------------------


def test_load_missing_file_returns_empty(tmp_path: Path) -> None:
    store = MessageStore(tmp_path)
    assert store.load() == []
    assert store.message_count == 0


def test_load_empty_file_returns_empty(tmp_path: Path) -> None:
    path = _jsonl(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("", encoding="utf-8")
    assert MessageStore(tmp_path).load() == []


# --- round-trip append/load ---------------------------------------------------------


def test_append_then_load_round_trip(tmp_path: Path) -> None:
    store = MessageStore(tmp_path)
    store.append([HumanMessage("привет"), AIMessage("здравствуй")])

    loaded = MessageStore(tmp_path).load()  # новый инстанс — поднимаем с диска
    assert [type(m).__name__ for m in loaded] == ["HumanMessage", "AIMessage"]
    assert [m.content for m in loaded] == ["привет", "здравствуй"]


def test_append_is_append_only(tmp_path: Path) -> None:
    """A second append adds to the file without overwriting the first."""
    store = MessageStore(tmp_path)
    store.append([HumanMessage("первое")])
    store.append([AIMessage("второе")])

    lines = _jsonl(tmp_path).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    loaded = MessageStore(tmp_path).load()
    assert [m.content for m in loaded] == ["первое", "второе"]


def test_message_count_tracks_and_restores(tmp_path: Path) -> None:
    store = MessageStore(tmp_path)
    store.append([HumanMessage("a"), AIMessage("b")])
    assert store.message_count == 2

    fresh = MessageStore(tmp_path)
    fresh.load()
    assert fresh.message_count == 2  # восстановлен из лога


# --- реконструкция от маркера-саммари (head) ----------------------------------------


def test_load_reconstructs_from_summary(tmp_path: Path) -> None:
    """Load = summary + tail from head; the (compacted) beginning is not returned."""
    store = MessageStore(tmp_path)
    # 5 сообщений: m0..m4
    store.append([HumanMessage(f"m{i}") for i in range(5)])
    # компакция: первые 3 свёрнуты, хвост — с индекса 3 (m3, m4)
    store.append_summary(SystemMessage("саммари m0-m2"), head=3)
    # после компакции пришло ещё одно сообщение
    store.append([HumanMessage("m5")])

    loaded = MessageStore(tmp_path).load()
    assert loaded[0].content == "саммари m0-m2"
    assert [m.content for m in loaded[1:]] == ["m3", "m4", "m5"]


def test_load_without_summary_returns_full_log(tmp_path: Path) -> None:
    store = MessageStore(tmp_path)
    store.append([HumanMessage(f"m{i}") for i in range(3)])
    loaded = MessageStore(tmp_path).load()
    assert [m.content for m in loaded] == ["m0", "m1", "m2"]


def test_last_summary_wins(tmp_path: Path) -> None:
    """With several compactions the reconstruction starts from the last summary."""
    store = MessageStore(tmp_path)
    store.append([HumanMessage(f"m{i}") for i in range(4)])  # m0..m3
    store.append_summary(SystemMessage("саммари-1"), head=2)
    store.append([HumanMessage("m4"), HumanMessage("m5")])   # m4, m5 (индексы 4,5)
    store.append_summary(SystemMessage("саммари-2"), head=5)

    loaded = MessageStore(tmp_path).load()
    assert loaded[0].content == "саммари-2"
    assert [m.content for m in loaded[1:]] == ["m5"]


# --- тяжёлый контент по ссылке ------------------------------------------------------


def test_heavy_base64_externalized_to_blob(tmp_path: Path) -> None:
    big = "data:image/png;base64," + "A" * 9000  # длиннее порога 8192
    store = MessageStore(tmp_path)
    store.append([HumanMessage(content=[{"type": "image_url", "image_url": {"url": big}}])])

    raw_log = _jsonl(tmp_path).read_text(encoding="utf-8")
    assert big not in raw_log               # инлайн base64 в логе НЕ лежит
    assert "blob:" in raw_log               # вместо него ссылка

    blobs = list((tmp_path / ".runtime" / "blobs").glob("*.b64"))
    assert len(blobs) == 1
    assert blobs[0].read_text(encoding="utf-8") == big  # сам blob сохранён целиком


def test_small_data_uri_stays_inline(tmp_path: Path) -> None:
    """A short data: URI below the threshold is not moved out."""
    small = "data:image/png;base64,AAAA"
    store = MessageStore(tmp_path)
    store.append([HumanMessage(content=[{"type": "image_url", "image_url": {"url": small}}])])

    raw_log = _jsonl(tmp_path).read_text(encoding="utf-8")
    assert small in raw_log
    assert not (tmp_path / ".runtime" / "blobs").exists()


# --- load_ui_messages: «вид» журнала для UI (DF-H) -----------------------------------


def test_ui_messages_missing_file(tmp_path: Path) -> None:
    assert MessageStore(tmp_path).load_ui_messages() == {"summary": None, "messages": []}


def test_ui_messages_without_summary_returns_raw_dicts(tmp_path: Path) -> None:
    """Records are returned as `messages_to_dict` dicts ({type, data}) without hydration."""
    store = MessageStore(tmp_path)
    store.append([HumanMessage("привет"), AIMessage("здравствуй")])

    view = MessageStore(tmp_path).load_ui_messages()
    assert view["summary"] is None
    assert [m["type"] for m in view["messages"]] == ["human", "ai"]
    assert view["messages"][0]["data"]["content"] == "привет"


def test_ui_messages_summary_separate_tail_from_head(tmp_path: Path) -> None:
    """Summary is a separate field (load() shows it as a human message); the tail starts at head."""
    store = MessageStore(tmp_path)
    store.append([HumanMessage(f"m{i}") for i in range(5)])
    store.append_summary(HumanMessage("[Сводка предыдущего диалога]\nсаммари"), head=3)
    store.append([HumanMessage("m5")])

    view = MessageStore(tmp_path).load_ui_messages()
    assert view["summary"]["data"]["content"].startswith("[Сводка")
    assert [m["data"]["content"] for m in view["messages"]] == ["m3", "m4", "m5"]


def test_ui_messages_last_summary_wins(tmp_path: Path) -> None:
    store = MessageStore(tmp_path)
    store.append([HumanMessage(f"m{i}") for i in range(4)])
    store.append_summary(SystemMessage("саммари-1"), head=2)
    store.append([HumanMessage("m4"), HumanMessage("m5")])
    store.append_summary(SystemMessage("саммари-2"), head=5)

    view = MessageStore(tmp_path).load_ui_messages()
    assert view["summary"]["data"]["content"] == "саммари-2"
    assert [m["data"]["content"] for m in view["messages"]] == ["m5"]


def test_ui_messages_json_clean(tmp_path: Path) -> None:
    """The dicts came from json.loads → serializable back without default=str."""
    store = MessageStore(tmp_path)
    store.append([AIMessage(
        content="",
        tool_calls=[{"name": "edit", "args": {"operations": []}, "id": "c1",
                     "type": "tool_call"}],
    )])
    json.dumps(MessageStore(tmp_path).load_ui_messages())  # не кидает


def test_ui_messages_does_not_touch_message_count(tmp_path: Path) -> None:
    """load_ui_messages is a pure read: it does not touch the write-path counter."""
    store = MessageStore(tmp_path)
    store.append([HumanMessage("a")])
    assert store.message_count == 1
    store.load_ui_messages()
    assert store.message_count == 1


# --- _read_tolerant: конкурентный append -------------------------------------------


def test_ui_messages_torn_last_line_skipped(tmp_path: Path) -> None:
    """A half-written last line (the reader caught an append midway) is skipped
    silently; whole records are returned."""
    store = MessageStore(tmp_path)
    store.append([HumanMessage("целое")])
    with _jsonl(tmp_path).open("a", encoding="utf-8") as f:
        f.write('{"kind": "message", "data": {"ty')  # без \n и закрывающих скобок

    view = MessageStore(tmp_path).load_ui_messages()
    assert [m["data"]["content"] for m in view["messages"]] == ["целое"]


def test_ui_messages_corruption_mid_file_raises(tmp_path: Path) -> None:
    """A broken line in the MIDDLE is log corruption, not a race: it must not be silenced."""
    store = MessageStore(tmp_path)
    store.append([HumanMessage("a"), HumanMessage("b")])
    path = _jsonl(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    lines.insert(1, "{broken")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(json.JSONDecodeError):
        MessageStore(tmp_path).load_ui_messages()
