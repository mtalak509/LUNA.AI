"""CLI input on prompt_toolkit: the prompt, completion, keys, and the status line.

On a terminal this is a `PromptSession` with history in `~/.luna/cli/history`, multiline
input (alt+enter), completion of `/commands`, workspace `@files`, and command arguments,
plus a bottom line with the modes and the model. Without a terminal (a pipe, tests) it
falls back to plain `input()`.

HITL questions live here too (`ask_text`, `ask_choice`): an arrow menu on a terminal, an
option number without one.
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
MAX_WORKSPACE_FILES = 2000  # cap on the workspace walk for @-completion

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
    """`provider · model` for the status line; empty when the config cannot be read."""
    try:
        from core.config import get_config

        cfg = get_config()
        provider = cfg.llm_provider
        return f"{provider} · {getattr(cfg, provider).llm_model}"
    except Exception:  # noqa: BLE001 — the status line must not take down the CLI
        return ""


# --- completion -------------------------------------------------------------------------------


def workspace_files(root: Path, limit: int = MAX_WORKSPACE_FILES) -> list[str]:
    """Workspace file and directory paths relative to the root, excluding `.runtime`."""
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
    """`/commands` with their descriptions, their arguments, and `@path` inside the workspace."""

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
        except Exception:  # noqa: BLE001 — no checkpoint directory, so nothing to offer
            return
        for c in reversed(snaps):
            if c.id.startswith(typed):
                yield Completion(
                    c.id, start_position=-len(typed), display_meta=f"{c.created_at:%H:%M:%S}"
                )


# --- prompt -----------------------------------------------------------------------------------


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
                "   ·   shift+tab mode · alt+enter new line · / commands · @ files",
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
        # The menu is open: the item chosen with the arrows is already inserted — close the
        # menu. Nothing chosen — take the first item if it adds something. Otherwise (the
        # command is complete, or the argument is empty) enter submits the line.
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
        # A non-empty line is just cleared; an empty one signals quit (a second ctrl+c).
        buf = event.current_buffer
        if buf.text:
            buf.reset()
        else:
            event.app.exit(exception=KeyboardInterrupt)

    return kb


class Prompter:
    """Read the user's line: prompt_toolkit on a terminal, `input()` without one."""

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


# --- HITL questions ---------------------------------------------------------------------------


async def ask_text(message: str) -> str:
    """A free-text answer to the agent's question."""
    if not is_interactive():
        return (await asyncio.to_thread(input, f"  {message} > ")).strip()
    session: PromptSession = PromptSession(style=STYLE)
    return (await session.prompt_async([("class:prompt", f"  {message} > ")])).strip()


async def ask_choice(message: str, options: Sequence[tuple[Any, str]]) -> Any:
    """Pick one option: arrows and enter on a terminal, a number without one."""
    if is_interactive():
        return await ChoiceInput(
            message=message, options=list(options), symbol="❯", style=STYLE
        ).prompt_async()
    for i, (_, label) in enumerate(options, 1):
        print(f"    [{i}] {label}")
    while True:
        raw = (await asyncio.to_thread(input, "  choice > ")).strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1][0]
        print("    enter an option number")
