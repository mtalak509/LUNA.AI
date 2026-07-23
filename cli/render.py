"""Рендер стрима в консоль: StreamEvent → текст по `mode` (см. run_stream).

Раскладка data по mode:
  messages — токен-дельта основного/суб-агента; updates — вызовы тулов и их результаты;
  custom   — события делегации (мультиплекс субагента) и прочие dict-события тулов.
"""

from __future__ import annotations

YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"
TOOL_SEPARATOR = "  " + "-" * 88


def yellow(text: str) -> str:
    return f"{YELLOW}{text}{RESET}"


def cyan(text: str) -> str:
    return f"{CYAN}{text}{RESET}"


def _render_messages(data) -> None:
    """mode='messages' → токен-дельта. ToolMessage идёт ниже из updates, его пропускаем."""
    chunk, meta = data
    if (meta or {}).get("langgraph_node") == "tools":
        return
    text = getattr(chunk, "text", "") or ""
    if text:
        print(text, end="", flush=True)


def _render_updates(data, indent: str = "", color=yellow) -> None:
    """mode='updates' → вызовы тулов (model) и их результаты (tools).

    `color` — раскраска тул-строк: основной агент жёлтым, субагент бирюзовым (cyan),
    чтобы вложенная работа делегата визуально отличалась от тулколлов MainAgent.
    """
    for source, update in data.items():
        for m in (update or {}).get("messages", []):
            if source == "model":
                for tc in getattr(m, "tool_calls", None) or []:
                    print(color(f"\n{indent}{TOOL_SEPARATOR}"))
                    print(color(f"{indent}  → tool: {tc.get('name')}({tc.get('args')})"))
            else:  # tools
                status = getattr(m, "status", None) or "ok"
                print(color(f"\n{indent}  ← {getattr(m, 'name', '?')} [{status}]: {m.content}"))
                print(color(f"{indent}{TOOL_SEPARATOR}"))


def _render_custom(data) -> None:
    """custom-канал: события делегации (мультиплекс субагента) + прочие dict-события тулов.

    Конверты субагента (`delegate_to_subagent`) распаковываем и рендерим ТЕМИ ЖЕ
    _render_messages/_render_updates, что и main — но с отступом и маркерами зоны, чтобы
    вложенная работа субагента читалась, а не сыпалась сырыми AIMessageChunk'ами.
    """
    if not isinstance(data, dict):
        print(f"\n[custom] {data}")
        return

    etype = data.get("type")
    if etype == "namespace_open":
        print(cyan(f"\n  ┌─ субагент {data.get('namespace')} запущен"))
    elif etype == "namespace_close":
        err = data.get("error")
        print(
            cyan(
                f"\n  └─ субагент {data.get('namespace')} завершён"
                + (f" — ошибка: {err}" if err else "")
            )
        )
    elif etype == "subagent_event":
        mode, inner = data.get("mode"), data.get("data")
        if mode == "messages":
            _render_messages(inner)
        elif mode == "updates":
            _render_updates(inner, indent="    ", color=cyan)
        elif mode == "custom":
            _render_custom(inner)  # вложенный custom субагента (напр. document_patch)
        # mode == "error" и прочее у субагента — игнорируем шум в CLI
    elif etype == "document_patch":
        print(yellow(f"\n  ✎ document_patch: {len(data.get('operations', []))} оп."))
    else:
        print(f"\n[custom] {data}")


def render(ev) -> None:
    """Один StreamEvent → консоль. Раскладка data по mode — см. run_stream."""
    if ev.mode == "messages":
        _render_messages(ev.data)
    elif ev.mode == "updates":
        _render_updates(ev.data)
    elif ev.mode == "custom":
        _render_custom(ev.data)
    elif ev.mode == "error":
        print(f"\n[ERROR] {ev.data}")
