"""Render a turn's stream to the console with rich: StreamEvent → scrollback blocks.

How `data` is laid out by mode (see run_stream):
  messages — token deltas of the main agent; updates — tool calls (model) and their
  results (tools); custom — delegation events (the subagent multiplex), document_patch,
  and other dict events from tools; error — the turn failed.

Everything printed stays in the terminal scrollback. The only live region (rich.Live,
transient) is the bottom of the screen: the tail of the markdown being written and the
spinner line with a timer. Settled answer lines move into the scrollback as they stream,
so a long answer is not limited by the window height.
"""

from __future__ import annotations

import difflib
import time
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.segment import Segment
from rich.spinner import Spinner
from rich.text import Text

console = Console(highlight=False)

ARGS_WIDTH = 90  # tool arguments on the call line
RESULT_LINES = 3  # result lines shown under a call
RESULT_WIDTH = 160  # result line width before it is cut
DIFF_LINES = 40  # diff lines of `edit` shown before the cut
LIVE_TAIL = 8  # markdown lines that may still be redrawn (they live in Live)
RENDER_EVERY = 0.08  # seconds; markdown is not redrawn more often than this while streaming

MAIN = ("", "yellow")
SUB = ("  │ ", "cyan")


def shorten(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def summarize_args(args: Any, width: int = ARGS_WIDTH, skip: tuple[str, ...] = ()) -> str:
    """Call arguments on one line: `path='report.json', limit=5`, cut to the width."""
    if not isinstance(args, dict):
        return shorten(str(args), width)
    parts = []
    for key, value in args.items():
        if key in skip:
            continue
        shown = repr(value) if isinstance(value, str) else str(value)
        parts.append(f"{key}={shorten(shown, 40)}")
    return shorten(", ".join(parts), width)


def summarize_result(content: Any) -> tuple[list[str], int]:
    """The first lines of a tool result, and how many lines were hidden."""
    if isinstance(content, list):  # message content blocks
        content = "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    lines = [ln for ln in str(content).splitlines() if ln.strip()] or ["(empty)"]
    shown = [shorten(ln, RESULT_WIDTH) for ln in lines[:RESULT_LINES]]
    return shown, len(lines) - len(shown)


def edit_diff(name: str, args: Any) -> list[Text]:
    """Colored unified diff for `edit` (old_string → new_string); empty for any other tool."""
    if name != "edit" or not isinstance(args, dict):
        return []
    old, new = args.get("old_string"), args.get("new_string")
    if not isinstance(old, str) or not isinstance(new, str):
        return []
    lines = []
    for ln in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=1):
        if ln.startswith(("---", "+++")):
            continue
        style = "green" if ln.startswith("+") else "red" if ln.startswith("-") else "dim"
        lines.append(Text(ln, style=style))
    if len(lines) > DIFF_LINES:
        hidden = len(lines) - DIFF_LINES
        lines = lines[:DIFF_LINES] + [Text(_more(hidden, "diff line"), style="dim")]
    return lines


def _more(count: int, noun: str) -> str:
    """`… 3 more lines`. `noun` is the singular form."""
    return f"… {count} more {noun if count == 1 else noun + 's'}"


def _chunk_text(chunk: Any) -> str:
    return getattr(chunk, "text", "") or ""


def _is_reasoning(chunk: Any) -> bool:
    content = getattr(chunk, "content", None)
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "reasoning" for b in content
    )


def _indent(line: list[Segment]) -> Text:
    """One rendered markdown line → Text with the answer indent, trailing spaces stripped."""
    text = Text("  ")
    for seg in line:
        if not seg.control:
            text.append(seg.text, seg.style)
    text.rstrip()
    return text


class MarkdownStream:
    """Streaming markdown: settled lines go to the scrollback, the tail stays in Live.

    Markdown is re-rendered as a whole (a table or a list can change lines already shown),
    so the last LIVE_TAIL lines stay live until the block ends.
    """

    def __init__(self, out: Console) -> None:
        self._out = out
        self._text = ""
        self._printed = 0
        self._last_render = 0.0
        self.tail: RenderableType | None = None

    def feed(self, chunk: str) -> None:
        self._text += chunk
        now = time.monotonic()
        if now - self._last_render >= RENDER_EVERY:
            self._last_render = now
            self._render(final=False)

    def finish(self) -> None:
        self._render(final=True)
        self.tail = None

    def _render(self, *, final: bool) -> None:
        if not self._text.strip():
            return
        options = self._out.options.update(width=max(20, self._out.width - 2))
        lines: list[Text] = []
        for segments in self._out.render_lines(Markdown(self._text), options, pad=False):
            line = _indent(segments)
            # A table border and a paragraph indent produce blank lines in a row — keep one.
            if line.plain.strip() or (lines and lines[-1].plain.strip()):
                lines.append(line)
        stable = len(lines) if final else max(0, len(lines) - LIVE_TAIL)
        for line in lines[self._printed : stable]:
            self._out.print(line)
        self._printed = max(self._printed, stable)
        tail = lines[self._printed :]
        self.tail = Group(*tail) if tail else None


class TurnView:
    """One turn's view: print events into the scrollback and keep a Live spinner at the bottom."""

    def __init__(self, out: Console | None = None) -> None:
        self.out = out or console
        self._live: Live | None = None
        self._md: MarkdownStream | None = None
        self._sub_text: dict[str, str] = {}
        self._t0 = time.monotonic()
        self._block: str | None = None
        self.activity = "thinking"
        # One instance for the whole turn: a Spinner counts frames from its first render.
        self._spinner = Spinner("dots", style="yellow")

    # --- Live -----------------------------------------------------------------------------

    def start(self) -> None:
        self._t0 = time.monotonic()
        self._block = None
        self._print()
        self.resume()

    def pause(self) -> None:
        """Remove the Live bottom (before a HITL question: input must not fight the spinner)."""
        self._flush_md()
        if self._live is not None:
            self._live.stop()
            self._live = None

    def resume(self) -> None:
        if self._live is None and self.out.is_terminal:
            self._live = Live(
                console=self.out,
                transient=True,
                refresh_per_second=8,
                get_renderable=self._bottom,
            )
            self._live.start()

    def stop(self) -> None:
        self._flush_md()
        for ns in list(self._sub_text):
            self._flush_sub(ns)
        self.pause()

    def _bottom(self) -> RenderableType:
        elapsed = int(time.monotonic() - self._t0)
        self._spinner.update(
            text=Text.assemble(
                (f"{self.activity}…", "yellow"), (f"  ({elapsed}s · esc — interrupt)", "dim")
            )
        )
        tail = self._md.tail if self._md is not None else None
        return Group(tail, self._spinner) if tail is not None else self._spinner

    # --- printing -----------------------------------------------------------------------------

    def _print(self, *renderables: RenderableType) -> None:
        self.out.print(*renderables)

    def _flush_md(self) -> None:
        if self._md is not None:
            self._md.finish()
            self._md = None

    def _flush_sub(self, ns: str) -> None:
        text = self._sub_text.pop(ns, "").strip()
        for ln in text.splitlines():
            self._print(Text.assemble((SUB[0], SUB[1]), (ln, "dim")))

    def _begin_block(self, kind: str) -> None:
        """Separate the answer from tool output with one blank line.

        The blank under the user's prompt is printed in `start`. This only fires when
        the turn switches from one kind of block to the other.
        """
        if self._block == kind:
            return
        if self._block is not None:
            self._print()
        self._block = kind

    def note(self, text: str, style: str = "yellow") -> None:
        self._flush_md()
        self._begin_block("tools")
        self._print(Text(text, style=style))

    # --- events -------------------------------------------------------------------------------

    def handle(self, ev: Any) -> None:
        """One StreamEvent from the main agent → the console."""
        if ev.mode == "messages":
            self._main_message(ev.data)
        elif ev.mode == "updates":
            self._flush_md()
            self._updates(ev.data, MAIN)
        elif ev.mode == "custom":
            self._custom(ev.data)
        elif ev.mode == "error":
            self._flush_md()
            data = ev.data
            msg = data.get("error", data) if isinstance(data, dict) else data
            self._begin_block("tools")
            self._print(Text(f"✗ error: {msg}", style="bold red"))

    def _main_message(self, data: Any) -> None:
        chunk, meta = data
        if (meta or {}).get("langgraph_node") == "tools":
            return  # the ToolMessage arrives below, from updates
        if _is_reasoning(chunk):
            self.activity = "reasoning"
        text = _chunk_text(chunk)
        if text:
            self.activity = "writing"
            self._begin_block("answer")
            if self._md is None:
                self._md = MarkdownStream(self.out)
            self._md.feed(text)

    def _updates(self, data: Any, zone: tuple[str, str]) -> None:
        """Tool calls (model) and their results (tools), one line each."""
        prefix, color = zone
        for source, update in (data or {}).items():
            for m in (update or {}).get("messages", []):
                if source == "model":
                    for tc in getattr(m, "tool_calls", None) or []:
                        name, args = tc.get("name") or "?", tc.get("args")
                        diff = edit_diff(name, args)
                        # old/new_string are already in the diff — skip them on the call line
                        skip = ("old_string", "new_string") if diff else ()
                        self.activity = f"running {name}"
                        self._begin_block("tools")
                        self._print(
                            Text.assemble(
                                (prefix, color),
                                ("● ", color),
                                (name, "bold"),
                                (f"({summarize_args(args, skip=skip)})", "dim"),
                            )
                        )
                        for diff_line in diff:
                            self._print(Text.assemble((prefix, color), "    ", diff_line))
                elif source == "tools":
                    failed = getattr(m, "status", None) == "error"
                    lines, hidden = summarize_result(getattr(m, "content", ""))
                    style = "red" if failed else "dim"
                    self._begin_block("tools")
                    for i, ln in enumerate(lines):
                        lead = "  ⎿  " if i == 0 else "     "
                        self._print(Text.assemble((prefix, color), (lead + ln, style)))
                    if hidden:
                        self._print(
                            Text.assemble((prefix, color), (f"     {_more(hidden, 'line')}", "dim"))
                        )
                    self.activity = "thinking"

    def _custom(self, data: Any, zone: tuple[str, str] = MAIN) -> None:
        """The custom channel: subagent delegation, document_patch, and other dict events."""
        prefix, color = zone
        if not isinstance(data, dict):
            self._flush_md()
            self._begin_block("tools")
            self._print(Text(f"{prefix}[custom] {data}", style="dim"))
            return

        etype = data.get("type")
        if etype == "namespace_open":
            self._flush_md()
            ns = data.get("namespace")
            self.activity = f"subagent {ns}"
            self._begin_block("tools")
            self._print(Text(f"  ┌─ subagent {ns} started", style="cyan"))
        elif etype == "namespace_close":
            ns = data.get("namespace") or ""
            self._begin_block("tools")
            self._flush_sub(ns)
            err = data.get("error")
            self._print(
                Text(
                    f"  └─ subagent {ns} finished" + (f" — error: {err}" if err else ""),
                    style="red" if err else "cyan",
                )
            )
            self.activity = "thinking"
        elif etype == "subagent_event":
            ns = data.get("namespace") or ""
            mode, inner = data.get("mode"), data.get("data")
            if mode == "messages":
                chunk, meta = inner or ((), None)
                if (meta or {}).get("langgraph_node") != "tools":
                    self._sub_text[ns] = self._sub_text.get(ns, "") + _chunk_text(chunk)
            elif mode == "updates":
                self._flush_sub(ns)
                self._updates(inner, SUB)
            elif mode == "custom":
                self._flush_sub(ns)
                self._custom(inner, SUB)  # nested custom event, e.g. document_patch
            # a subagent's error mode is noise; the outcome arrives in namespace_close
        elif etype == "document_patch":
            self._flush_md()
            self._begin_block("tools")
            ops = len(data.get("operations", []))
            self._print(
                Text.assemble(
                    (prefix, color),
                    ("✎ json_patch ", color),
                    (str(data.get("file", "")), "bold"),
                    (f": {ops} ops", "dim"),
                )
            )
        else:
            self._flush_md()
            self._begin_block("tools")
            self._print(Text(f"{prefix}[custom] {data}", style="dim"))
