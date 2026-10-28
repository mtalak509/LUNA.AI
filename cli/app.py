"""Точка входа CLI: цикл REPL поверх раннера AgentSession.

Гоняет ход через `AgentSession` (ход как asyncio.Task + очередь событий) и печатает стрим
через rich (`cli/render.py`). Ввод — prompt_toolkit (`cli/prompt.py`): история, `/команды`
и `@файлы` с автодополнением, строка состояния. Раннер развязывает ход и потребителя,
поэтому CLI умеет отвечать на HITL и прерывать ход: на событие `hitl_*` он читает ответ и
зовёт `session.resolve_hitl(...)`, на esc/ctrl+c — `session.cancel_turn()`. Без
HTTP/оркестратора; нужен поднятый LLM-провайдер (.env).

Запуск:  python -m cli   (или консольная команда `luna`)
Команды: /plan /act  /confirm /accept  /attach <path>  /fs [path]  /cp  /undo <id>
         /reset  /help  /quit
Клавиши: esc/ctrl+c — прервать ход; shift+tab — plan/act; alt+enter — новая строка;
         ctrl+c дважды на пустой строке или ctrl+d — выход.
"""

from __future__ import annotations

import asyncio
import logging
import time

from rich.text import Text

from cli.banner import print_banner
from cli.commands import handle_command
from cli.config import SESSION_DIR, SESSION_ROOT, state
from cli.prompt import Prompter
from cli.render import console
from cli.session import bootstrap_process, new_session
from cli.turn import drive_turn

EXIT_WINDOW = 2.0  # с между двумя ctrl+c на пустой строке, чтобы выйти


async def main() -> None:
    # Логи — в файл, не в консоль: CLI показывает только рендер, а трейсы реальных багов
    # ложатся в repl.log (штатные отказы тулов — тихий debug, см. ToolErrorMiddleware).
    # mkdir до basicConfig: файл-handler открывает лог сразу
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, filename=SESSION_ROOT / "repl.log", encoding="utf-8")

    console.print(Text(print_banner(), style="yellow"))

    # Явная пара init/build — процесс один раз, сессия отдельно (как в server.py).
    bootstrap_process()  # init_process → state["procedures_root"]
    state["session"] = new_session()  # создаёт workspace/ + раннер поверх MainAgent
    console.print(
        Text.assemble(
            f"CLI готов (session={SESSION_DIR}). ",
            ("/help — команды, /quit — выход.", "dim"),
        )
    )

    prompter = Prompter()
    last_interrupt = 0.0
    while True:
        try:
            line = (await prompter.read()).strip()
        except KeyboardInterrupt:
            now = time.monotonic()
            if now - last_interrupt < EXIT_WINDOW:
                break
            last_interrupt = now
            console.print(Text("ctrl+c ещё раз — выход", style="dim"))
            continue
        except (EOFError, asyncio.CancelledError):
            break

        if not line:
            continue

        if line.startswith("/"):
            if not handle_command(line):
                break
            continue

        # Режимы могли смениться slash-командой — подаём актуальные в раннер на этот ход.
        session = state["session"]
        session.permission_mode = state["perm"]
        session.decision_mode = state["dec"]
        try:
            await drive_turn(session, line)
            console.print()
        except Exception as e:  # dev-tool: не роняем CLI на сбое хода (коннект к LLM и пр.)
            console.print(Text(f"\n[ход упал] {type(e).__name__}: {e}", style="bold red"))


def run() -> None:
    """Синхронная обёртка для точки входа (`python -m cli` / консольный скрипт `luna`)."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    run()
