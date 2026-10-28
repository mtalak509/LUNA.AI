"""Рендер стрима хода в консоль на rich: StreamEvent → блоки в обычной прокрутке терминала.

Раскладка data по mode (см. run_stream):
  messages — токен-дельта основного агента; updates — вызовы тулов (model) и их результаты
  (tools); custom — события делегации (мультиплекс субагента), document_patch и прочие
  dict-события тулов; error — сбой хода.

Всё напечатанное остаётся в истории терминала. «Живым» (rich.Live, transient) остаётся только
низ экрана: хвост отвечаемого markdown и строка-спиннер с таймером. Устоявшиеся строки
ответа уходят в прокрутку по мере стрима, поэтому длинный ответ не упирается в высоту окна.
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

ARGS_WIDTH = 90  # аргументы тула в строке вызова
RESULT_LINES = 3  # строк результата тула под вызовом
RESULT_WIDTH = 160  # ширина строки результата до обрезки
DIFF_LINES = 40  # diff lines of `edit` shown before the cut
LIVE_TAIL = 8  # строк markdown, которые ещё могут перерисоваться (живут в Live)
RENDER_EVERY = 0.08  # с; не чаще перерисовываем markdown на стриме токенов

MAIN = ("", "yellow")
SUB = ("  │ ", "cyan")


def shorten(text: str, width: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 1] + "…"


def summarize_args(args: Any, width: int = ARGS_WIDTH, skip: tuple[str, ...] = ()) -> str:
    """Аргументы вызова одной строкой: `path='report.json', limit=5`, обрезано по ширине."""
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
    """Первые строки результата тула и сколько строк скрыто."""
    if isinstance(content, list):  # content-блоки сообщения
        content = "\n".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    lines = [ln for ln in str(content).splitlines() if ln.strip()] or ["(пусто)"]
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
        lines = lines[:DIFF_LINES] + [Text(f"… ещё {hidden} строк diff", style="dim")]
    return lines


def _chunk_text(chunk: Any) -> str:
    return getattr(chunk, "text", "") or ""


def _is_reasoning(chunk: Any) -> bool:
    content = getattr(chunk, "content", None)
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "reasoning" for b in content
    )


def _indent(line: list[Segment]) -> Text:
    """Строка отрендеренного markdown → Text с отступом ответа, без хвостовых пробелов."""
    text = Text("  ")
    for seg in line:
        if not seg.control:
            text.append(seg.text, seg.style)
    text.rstrip()
    return text


class MarkdownStream:
    """Потоковый markdown: устоявшиеся строки печатаются в прокрутку, хвост отдаётся в Live.

    Markdown перерисовывается целиком (таблица или список могут поменять уже показанные
    строки), поэтому последние LIVE_TAIL строк остаются «живыми» до конца блока.
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
            # Рамка таблицы и отступ абзаца дают пустые строки подряд — оставляем одну.
            if line.plain.strip() or (lines and lines[-1].plain.strip()):
                lines.append(line)
        stable = len(lines) if final else max(0, len(lines) - LIVE_TAIL)
        for line in lines[self._printed : stable]:
            self._out.print(line)
        self._printed = max(self._printed, stable)
        tail = lines[self._printed :]
        self.tail = Group(*tail) if tail else None


class TurnView:
    """Вид одного хода: печатает события в прокрутку и держит Live-низ со спиннером."""

    def __init__(self, out: Console | None = None) -> None:
        self.out = out or console
        self._live: Live | None = None
        self._md: MarkdownStream | None = None
        self._sub_text: dict[str, str] = {}
        self._t0 = time.monotonic()
        self.activity = "думаю"
        # One instance for the whole turn: a Spinner counts frames from its first render.
        self._spinner = Spinner("dots", style="yellow")

    # --- Live -----------------------------------------------------------------------------

    def start(self) -> None:
        self._t0 = time.monotonic()
        self.resume()

    def pause(self) -> None:
        """Убрать Live-низ (перед HITL-вопросом: ввод не должен драться со спиннером)."""
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
                (f"{self.activity}…", "yellow"), (f"  ({elapsed} с · esc — прервать)", "dim")
            )
        )
        tail = self._md.tail if self._md is not None else None
        return Group(tail, self._spinner) if tail is not None else self._spinner

    # --- печать ---------------------------------------------------------------------------

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

    def note(self, text: str, style: str = "yellow") -> None:
        self._flush_md()
        self._print(Text(text, style=style))

    # --- события --------------------------------------------------------------------------

    def handle(self, ev: Any) -> None:
        """Один StreamEvent основного агента → консоль."""
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
            self._print(Text(f"✗ ошибка: {msg}", style="bold red"))

    def _main_message(self, data: Any) -> None:
        chunk, meta = data
        if (meta or {}).get("langgraph_node") == "tools":
            return  # ToolMessage придёт ниже из updates
        if _is_reasoning(chunk):
            self.activity = "размышляю"
        text = _chunk_text(chunk)
        if text:
            self.activity = "пишу ответ"
            if self._md is None:
                self._md = MarkdownStream(self.out)
            self._md.feed(text)

    def _updates(self, data: Any, zone: tuple[str, str]) -> None:
        """Вызовы тулов (model) и их результаты (tools) одной строкой каждый."""
        prefix, color = zone
        for source, update in (data or {}).items():
            for m in (update or {}).get("messages", []):
                if source == "model":
                    for tc in getattr(m, "tool_calls", None) or []:
                        name, args = tc.get("name") or "?", tc.get("args")
                        diff = edit_diff(name, args)
                        # old/new_string уже видны в diff — в строке вызова их не дублируем
                        skip = ("old_string", "new_string") if diff else ()
                        self.activity = f"выполняю {name}"
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
                    for i, ln in enumerate(lines):
                        lead = "  ⎿  " if i == 0 else "     "
                        self._print(Text.assemble((prefix, color), (lead + ln, style)))
                    if hidden:
                        self._print(
                            Text.assemble((prefix, color), (f"     … ещё {hidden} строк", "dim"))
                        )
                    self.activity = "думаю"

    def _custom(self, data: Any, zone: tuple[str, str] = MAIN) -> None:
        """custom-канал: делегация субагенту, document_patch и прочие dict-события."""
        prefix, color = zone
        if not isinstance(data, dict):
            self._flush_md()
            self._print(Text(f"{prefix}[custom] {data}", style="dim"))
            return

        etype = data.get("type")
        if etype == "namespace_open":
            self._flush_md()
            ns = data.get("namespace")
            self.activity = f"работает субагент {ns}"
            self._print(Text(f"  ┌─ субагент {ns} запущен", style="cyan"))
        elif etype == "namespace_close":
            ns = data.get("namespace") or ""
            self._flush_sub(ns)
            err = data.get("error")
            self._print(
                Text(
                    f"  └─ субагент {ns} завершён" + (f" — ошибка: {err}" if err else ""),
                    style="red" if err else "cyan",
                )
            )
            self.activity = "думаю"
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
                self._custom(inner, SUB)  # вложенный custom субагента (напр. document_patch)
            # mode == "error" и прочее у субагента — шум, его итог придёт в namespace_close
        elif etype == "document_patch":
            self._flush_md()
            ops = len(data.get("operations", []))
            self._print(
                Text.assemble(
                    (prefix, color),
                    ("✎ json_patch ", color),
                    (str(data.get("file", "")), "bold"),
                    (f": {ops} оп.", "dim"),
                )
            )
        else:
            self._flush_md()
            self._print(Text(f"{prefix}[custom] {data}", style="dim"))
