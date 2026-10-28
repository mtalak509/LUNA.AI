"""Ввод CLI на prompt_toolkit: приглашение, автодополнение, клавиши, строка состояния.

На терминале — `PromptSession` с историей в `~/.luna/cli/history`, многострочным вводом
(alt+enter), автодополнением `/команд`, `@файлов` workspace и аргументов команд, и нижней
строкой с режимами и моделью. Без терминала (пайп, тесты) — обычный `input()`, как раньше.

Здесь же — вопросы HITL (`ask_text`, `ask_choice`): меню со стрелками на терминале,
номер варианта без него.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit.filters import has_completions
from prompt_toolkit.formatted_text import FormattedText
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.shortcuts.choice_input import ChoiceInput
from prompt_toolkit.styles import Style

from cli.commands import COMMANDS, checkpoint_manager
from cli.config import SESSION_DIR, SESSION_ROOT, state

HISTORY_FILE = SESSION_ROOT / "history"
MAX_WORKSPACE_FILES = 2000  # потолок обхода workspace для @-дополнения

STYLE = Style.from_dict(
    {
        "prompt": "ansiyellow bold",
        "bottom-toolbar": "noreverse #8a91a2",
        "toolbar.mode": "noreverse ansiyellow",
    }
)


def is_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def model_label() -> str:
    """`provider · model` для строки состояния; пусто, если конфиг не читается."""
    try:
        from core.config import get_config

        cfg = get_config()
        provider = cfg.llm_provider
        return f"{provider} · {getattr(cfg, provider).llm_model}"
    except Exception:  # noqa: BLE001 — строка состояния не должна ронять CLI
        return ""


# --- автодополнение --------------------------------------------------------------------------


def workspace_files(root: Path, limit: int = MAX_WORKSPACE_FILES) -> list[str]:
    """Пути файлов и папок workspace относительно корня, без `.runtime`."""
    found: list[str] = []
    if not root.is_dir():
        return found
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d != ".runtime")
        rel = Path(dirpath).relative_to(root)
        for d in dirnames:
            found.append((rel / d).as_posix() + "/")
        for f in sorted(filenames):
            found.append((rel / f).as_posix())
        if len(found) >= limit:
            break
    return found[:limit]


class LunaCompleter(Completer):
    """`/команды` с описанием, их аргументы и `@путь` внутри workspace."""

    def __init__(self, workspace: Path | None = None) -> None:
        self._workspace = workspace
        self._host_paths = PathCompleter(expanduser=True)

    @property
    def workspace(self) -> Path:
        return self._workspace or SESSION_DIR / "workspace"

    def get_completions(self, document: Document, complete_event: Any) -> Iterable[Completion]:
        text = document.text_before_cursor
        if text.startswith("/"):
            cmd, sep, arg = text.partition(" ")
            if not sep:
                yield from self._commands(cmd)
            elif cmd == "/attach":
                sub = Document(arg, len(arg))
                yield from self._host_paths.get_completions(sub, complete_event)
            elif cmd == "/fs":
                yield from self._paths(arg, prefix="", dirs_only=True)
            elif cmd == "/undo":
                yield from self._checkpoints(arg)
            return
        word = document.get_word_before_cursor(WORD=True)
        if word.startswith("@"):
            yield from self._paths(word[1:], prefix="@")

    def _commands(self, typed: str) -> Iterable[Completion]:
        for name, (args, desc) in COMMANDS.items():
            if name.startswith(typed):
                meta = f"{args}  {desc}" if args else desc
                yield Completion(name, start_position=-len(typed), display_meta=meta)

    def _paths(self, typed: str, *, prefix: str, dirs_only: bool = False) -> Iterable[Completion]:
        needle = typed.lower()
        for rel in workspace_files(self.workspace):
            if dirs_only and not rel.endswith("/"):
                continue
            if needle in rel.lower():
                yield Completion(
                    prefix + rel, start_position=-(len(typed) + len(prefix)), display=rel
                )

    def _checkpoints(self, typed: str) -> Iterable[Completion]:
        try:
            snaps = checkpoint_manager().list()
        except Exception:  # noqa: BLE001 — нет каталога чекпоинтов → нечего предлагать
            return
        for c in reversed(snaps):
            if c.id.startswith(typed):
                yield Completion(
                    c.id, start_position=-len(typed), display_meta=f"{c.created_at:%H:%M:%S}"
                )


# --- приглашение ----------------------------------------------------------------------------


def toggle_permission_mode() -> None:
    state["perm"] = "plan" if state["perm"] == "act" else "act"


def _toolbar() -> FormattedText:
    model = model_label()
    return FormattedText(
        [
            ("class:toolbar.mode", f" {state['perm']} · {state['dec']}"),
            ("class:bottom-toolbar", f"   {model}" if model else ""),
            (
                "class:bottom-toolbar",
                "   ·   shift+tab режим · alt+enter новая строка · / команды · @ файлы",
            ),
        ]
    )


def _key_bindings() -> KeyBindings:
    kb = KeyBindings()

    @kb.add("enter", filter=~has_completions)
    def _submit(event: Any) -> None:
        event.current_buffer.validate_and_handle()

    @kb.add("enter", filter=has_completions)
    def _take_completion(event: Any) -> None:
        # Меню открыто: выбранный стрелками вариант уже вставлен — закрыть меню; ничего не
        # выбрано — взять первый, если он что-то дописывает. Иначе (команда набрана целиком,
        # пустой аргумент) enter отправляет строку, как без меню.
        buf = event.current_buffer
        state_ = buf.complete_state
        if state_.current_completion is not None:
            buf.complete_state = None
            return
        first = state_.completions[0] if state_.completions else None
        before = buf.document.text_before_cursor
        typed = before[len(before) + first.start_position :] if first else ""
        if first is not None and typed and first.text != typed:
            buf.apply_completion(first)
        else:
            buf.validate_and_handle()

    @kb.add("escape", "enter")
    @kb.add("c-j")
    def _newline(event: Any) -> None:
        event.current_buffer.insert_text("\n")

    @kb.add("s-tab")
    def _toggle_mode(event: Any) -> None:
        toggle_permission_mode()
        event.app.invalidate()

    @kb.add("c-c")
    def _interrupt(event: Any) -> None:
        # Непустая строка — просто очистить; пустая — сигнал выхода (второй ctrl+c подряд).
        buf = event.current_buffer
        if buf.text:
            buf.reset()
        else:
            event.app.exit(exception=KeyboardInterrupt)

    return kb


class Prompter:
    """Чтение строки пользователя: prompt_toolkit на терминале, `input()` без него."""

    def __init__(self) -> None:
        self._session: PromptSession | None = None
        if is_interactive():
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            self._session = PromptSession(
                history=FileHistory(str(HISTORY_FILE)),
                completer=LunaCompleter(),
                complete_while_typing=True,
                multiline=True,
                prompt_continuation=lambda width, *_: "." * (width - 1) + " ",
                key_bindings=_key_bindings(),
                bottom_toolbar=_toolbar,
                style=STYLE,
            )

    async def read(self) -> str:
        if self._session is None:
            return await asyncio.to_thread(input, f"\n[{state['perm']}/{state['dec']}] > ")
        return await self._session.prompt_async([("class:prompt", "> ")])


# --- вопросы HITL ---------------------------------------------------------------------------


async def ask_text(message: str) -> str:
    """Свободный ответ на вопрос агента."""
    if not is_interactive():
        return (await asyncio.to_thread(input, f"  {message} > ")).strip()
    session: PromptSession = PromptSession(style=STYLE)
    return (await session.prompt_async([("class:prompt", f"  {message} > ")])).strip()


async def ask_choice(message: str, options: Sequence[tuple[Any, str]]) -> Any:
    """Выбор одного варианта: стрелки и enter на терминале, номер без него."""
    if is_interactive():
        return await ChoiceInput(
            message=message, options=list(options), symbol="❯", style=STYLE
        ).prompt_async()
    for i, (_, label) in enumerate(options, 1):
        print(f"    [{i}] {label}")
    while True:
        raw = (await asyncio.to_thread(input, "  выбор > ")).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print("    нужен номер варианта")
