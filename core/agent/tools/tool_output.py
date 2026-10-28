"""
Truncation of tool output: a large result must not land in the model context whole.

The tool declares its strategy with `@truncate_output`: `head` keeps the start, `tail` keeps the
end. Nothing infers it from the tool name, so a new tool does not silently get the wrong cut.
The full output goes to the agent's zone (`PathScope.spill_dir`) and the note tells the model
where it is, so the rest is reached with the ordinary file tools.
"""

from __future__ import annotations

import functools
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal
from uuid import uuid4

from langgraph.runtime import get_runtime

from core.agent.tools.fs_paths import SPILL_DIR, PathScope

_logger = logging.getLogger(__name__)

Strategy = Literal["head", "tail"]

MAX_LINES = 2000
MAX_BYTES = 50 * 1024


@dataclass(frozen=True)
class Cut:
    """The kept part of an output and how much of the whole it is."""

    text: str
    shown_lines: int
    total_lines: int
    shown_bytes: int
    total_bytes: int


class OutputTruncator:
    """Cuts a text to at most `max_lines` lines and `max_bytes` UTF-8 bytes.

    The cut is on a line boundary. Only when a single line alone is over the byte limit is it
    cut by bytes, and then never inside a character.
    """

    def __init__(
        self, strategy: Strategy, max_lines: int = MAX_LINES, max_bytes: int = MAX_BYTES
    ) -> None:
        if strategy not in ("head", "tail"):
            raise ValueError(f"unknown truncation strategy: {strategy!r}")
        self.strategy = strategy
        self._max_lines = max_lines
        self._max_bytes = max_bytes

    def cut(self, text: str) -> Cut | None:
        """The kept part, or None when the text is within both limits."""
        lines = text.splitlines(keepends=True)
        total_bytes = len(text.encode("utf-8"))
        if len(lines) <= self._max_lines and total_bytes <= self._max_bytes:
            return None

        if self.strategy == "head":
            kept = self._fit(lines) or [self._bytes_prefix(lines[0])]
        else:
            kept = self._fit(lines[::-1])[::-1] or [self._bytes_suffix(lines[-1])]
        shown = "".join(kept)
        return Cut(
            text=shown,
            shown_lines=len(kept),
            total_lines=len(lines),
            shown_bytes=len(shown.encode("utf-8")),
            total_bytes=total_bytes,
        )

    def _fit(self, lines: list[str]) -> list[str]:
        """Leading lines that fit both limits."""
        kept: list[str] = []
        size = 0
        for line in lines:
            n = len(line.encode("utf-8"))
            if len(kept) == self._max_lines or size + n > self._max_bytes:
                break
            kept.append(line)
            size += n
        return kept

    def _bytes_prefix(self, line: str) -> str:
        return line.encode("utf-8")[: self._max_bytes].decode("utf-8", errors="ignore")

    def _bytes_suffix(self, line: str) -> str:
        return line.encode("utf-8")[-self._max_bytes :].decode("utf-8", errors="ignore")


def truncate_output(
    strategy: Strategy,
) -> Callable[[Callable[..., Awaitable[str]]], Callable[..., Awaitable[str]]]:
    """Decorator over a tool that returns text: cut a large result and mark the cut.

    Order: `@agent_tool(...)` outside, `@truncate_output(...)` closer to the function.

    The full output is saved to `<zone>/.truncated_outputs/<tool>-<id>.txt` and the note gives
    that path. Tool errors pass through untouched. `read` does not use this decorator: its
    source is already a file, so it cuts its own window with `cut_window`.
    """
    truncator = OutputTruncator(strategy)

    def decorator(func: Callable[..., Awaitable[str]]) -> Callable[..., Awaitable[str]]:
        @functools.wraps(func)
        async def wrapper(*args: object, **kwargs: object) -> str:
            result = await func(*args, **kwargs)
            cut = truncator.cut(result)
            if cut is None:
                return result
            note = f"[вывод обрезан: {_extent(cut)}. {_spill_hint(result, func.__name__)}]"
            if strategy == "head":
                return f"{_with_newline(cut.text)}{note}"
            return f"{note}\n{cut.text}"

        return wrapper

    return decorator


_WINDOW_TRUNCATOR = OutputTruncator("head")


def cut_window(body: str, start: int, total: int) -> str | None:
    """Cut the lines `read` returns, starting at file line `start` of `total`; None if they fit.

    The header and the note are built after the cut, so they name the lines actually shown and
    the exact offset to go on from.
    """
    cut = _WINDOW_TRUNCATOR.cut(body)
    if cut is None:
        return None
    last = start + cut.shown_lines - 1
    note = (
        f"[вывод обрезан: показаны строки {start}–{last} из {total}. "
        f"Продолжай с offset={last + 1}.]"
    )
    return f"[строки {start}–{last} из {total}]\n{_with_newline(cut.text)}{note}"


def _extent(cut: Cut) -> str:
    return (
        f"показано {cut.shown_lines} из {cut.total_lines} строк, "
        f"{_size(cut.shown_bytes)} из {_size(cut.total_bytes)}"
    )


def _size(n: int) -> str:
    return f"{n} Б" if n < 1024 else f"{n / 1024:.0f} КБ"


def _with_newline(text: str) -> str:
    return text if text.endswith("\n") else f"{text}\n"


def _spill_hint(full: str, tool: str) -> str:
    """Save the full output in the agent's zone; the hint names its zone-relative path.

    A failed save does not fail the tool: the cut result is still useful.
    """
    spill_dir = PathScope.from_context(get_runtime().context).spill_dir
    name = f"{tool}-{uuid4().hex[:12]}.txt"
    try:
        spill_dir.mkdir(parents=True, exist_ok=True)
        (spill_dir / name).write_text(full, encoding="utf-8", newline="")
    except OSError as e:
        _logger.warning("truncated output of %s not saved: %s", tool, e)
        return "Полный вывод сохранить не удалось — сузь запрос."
    return f"Полный вывод: {SPILL_DIR}/{name} — читай его через read с offset/limit."
