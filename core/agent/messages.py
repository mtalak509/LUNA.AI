"""
MessageStore — durable-журнал диалога MainAgent на ФС (`messages.jsonl`).

Без checkpointer'а LangGraph граф между ходами stateless — историю держит рантайм
(`BaseAgent.self._messages`, рабочая копия в памяти). MessageStore — её durable-зеркало:
append-only лог, из которого копия поднимается при старте и после `/undo`.

Модель (паттерн Claude Code):
- рабочая копия в памяти — primary, подаётся в astream каждый ход;
- jsonl — append-only сырой лог, пишется из стрим-событий (подлинно новые сообщения),
  не переписывается;
- компакция персистится ОДИН раз: запись-саммари + указатель `head` на начало «хвоста».
  При загрузке копия = саммари + хвост (не пересуммаризируем каждый ход);
- только для MainAgent: субагент эфемерен, журнал ему не нужен.
"""

from __future__ import annotations

import json
from hashlib import sha1
from pathlib import Path

from langchain_core.messages import (
    BaseMessage,
    messages_from_dict,
    messages_to_dict,
)

# Два базовых типа записи в jsonl: обычное сообщение и маркер-саммари с указателем head.
_KIND_MESSAGE = "message"
_KIND_SUMMARY = "summary"

# Строки длиннее порога (база64 картинок от view_image) не храним инлайн в логе —
# выносим в blob-файл, в логе остаётся ссылка. Иначе jsonl и гидратация распухают.
_MAX_INLINE_CHARS = 8192


class MessageStore:
    """Durable-журнал диалога MainAgent на ФС (`messages.jsonl`). Принадлежит и пишет только MainAgent."""

    def __init__(self, workspace_path: Path) -> None:
        runtime_dir = Path(workspace_path) / ".runtime"
        self._path = runtime_dir / "messages.jsonl"
        self._blobs_dir = runtime_dir / "blobs"
        self._message_count = 0     # Счётчик сообщений для вычисления head при компакции

    @property
    def message_count(self) -> int:
        """Число message-записей в логе (рантайм считает по нему head для append_summary)."""
        return self._message_count

    # --- запись -------------------------------------------------

    def append(self, messages: list[BaseMessage]) -> None:
        """Записать сообщения в лог."""
        records = []
        for raw in messages_to_dict(list(messages)):
            records.append({"kind": _KIND_MESSAGE, "data": self._externalize(raw)})
        self._write(records)
        self._message_count += len(records)

    def append_summary(self, summary: BaseMessage, *, head: int) -> None:
        """Записать маркер компакции: саммари + указатель `head`.

        `head` — глобальный индекс в последовательности message-записей, с которого
        начинается удержанный «хвост» живого контекста (= message_count − len(хвоста)).
        Реконструкция возьмёт саммари + message-записи начиная с `head`.
        """
        data = messages_to_dict([summary])[0]
        self._write([{"kind": _KIND_SUMMARY, "data": data, "head": head}])

    # --- чтение -------------------------------------------------

    def load(self) -> list[BaseMessage]:
        """Загрузить сообщения из лога от последнего маркера компакции.

        Вызывается при старте процесса и после '/undo'.
        """
        records = self._read()
        self._message_count = sum(1 for r in records if r["kind"] == _KIND_MESSAGE)
        if not records:
            return []

        # Последний маркер компакции, если есть
        last_summary = next(
            (r for r in reversed(records) if r["kind"] == _KIND_SUMMARY),
            None,
        )
        msg_dicts = [r["data"] for r in records if r["kind"] == _KIND_MESSAGE]

        if last_summary is None:
            return messages_from_dict(msg_dicts)    # Весь сырой лог, если нет компакции

        summary_msg = messages_from_dict([last_summary["data"]])[0]
        tail = messages_from_dict(msg_dicts[last_summary["head"]:])
        return [summary_msg, *tail]

    def load_ui_messages(self) -> dict:
        """Read-only «вид» журнала для UI: сырые записи без гидратации
        
        Не используется в основном агентном цикле, но нужен для UI.
        """
        records = self._read_tolerant()
        msg_dics = [r["data"] for r in records if r["kind"] == _KIND_MESSAGE]
        last_summary = next(
            (r for r in reversed(records) if r["kind"] == _KIND_SUMMARY),
            None,   
        )
        if last_summary is None:
            return {"summary": None, "messages": msg_dics}
        return {
            "summary": last_summary["data"],
            "messages": msg_dics[last_summary["head"]:],
        }

    # --- helpers -------------------------------------------------

    def _write(self, records: list[dict]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def _read(self) -> list[dict]:
        if not self._path.exists():
            return []
        records = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                records.append(json.loads(line))
        return records

    def _externalize(self, data: dict) -> dict:
        """Рекурсивно вынести тяжёлые base64-строки в blob-файлы, оставив в логе ссылку.

        Детектируем по `data:`-URI + порог. На load ссылку НЕ инфлейтим обратно:
        журнал — для undo/аудита, медиа подгружают тулы.
        """
        def walk(node):
            if isinstance(node, str):
                if node.startswith("data:") and len(node) > _MAX_INLINE_CHARS:
                    return self._store_blob(node)
                return node
            if isinstance(node, list):
                return [walk(x) for x in node]
            if isinstance(node, dict):
                return {k: walk(v) for k, v in node.items()}
            return node

        return walk(data)

    def _store_blob(self, blob: str) -> str:
        self._blobs_dir.mkdir(parents=True, exist_ok=True)
        digest = sha1(blob.encode("utf-8")).hexdigest()
        (self._blobs_dir / f"{digest}.b64").write_text(blob, encoding="utf-8")
        return f"blob:{digest}"

    def _read_tolerant(self) -> list[dict]:
        """Как `_read`, но битая ПОСЛЕДНЯЯ строка молча пропускается.

        Журнал читается конкурентно с append'ом живого хода — читатель может поймать
        недописанную строку в хвосте. Битая строка в СЕРЕДИНЕ файла — по-прежнему
        ошибка (это не гонка, а порча журнала, глушить её нельзя).
        """
        if not self._path.exists():
            return []
        lines = [
            line for line in self._path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        records = []
        for i, line in enumerate(lines):
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                if i == len(lines) - 1:
                    break
                raise
        return records
