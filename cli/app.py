"""Точка входа CLI: цикл REPL поверх раннера AgentSession.

Гоняет ход через `AgentSession` (ход как asyncio.Task + очередь событий) и печатает стрим
по mode. Раннер развязывает ход и потребителя, поэтому CLI умеет отвечать на HITL: на
событие `hitl_*` он конкурентно читает ввод и зовёт `session.resolve_hitl(...)`, а
припаркованный ход продолжается. Без HTTP/оркестратора; нужен поднятый LLM-провайдер (.env).

Запуск:  python -m cli   (или консольная команда `luna`)
Команды: /plan /act  /confirm /accept  /ptr [pointer]  /fs [path]  /cp  /undo <id>
         /reset  /help  /quit
HITL:    на вопрос агента отвечай в приглашении `ответ >` / `выбор >`.
/ptr:    эмуляция «раздела, открытого в UI» — липнет ко всем следующим ходам;
         `/ptr` без аргумента сбрасывает.
"""

from __future__ import annotations

import asyncio
import logging

from cli.banner import print_banner
from cli.commands import handle_command
from cli.config import SESSION_DIR, SESSION_ROOT, state
from cli.session import bootstrap_process, new_session
from cli.turn import drive_turn


async def main() -> None:
    # Логи — в файл, не в консоль: CLI показывает только render(), а трейсы реальных багов
    # ложатся в repl.log (штатные отказы тулов — тихий debug, см. ToolErrorMiddleware).
    # mkdir до basicConfig: файл-handler открывает лог сразу
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, filename=SESSION_ROOT / "repl.log", encoding="utf-8")

    print(print_banner())

    # Явная пара init/build — процесс один раз, сессия отдельно (как в server.py).
    bootstrap_process()  # init_process → state["procedures_root"]
    state["session"] = new_session()  # создаёт workspace/ + раннер поверх MainAgent
    print(f"CLI готов (session={SESSION_DIR}). /help — команды, /quit — выход.")

    while True:
        try:
            ptr_mark = f" ptr={state['ptr']}" if state["ptr"] else ""
            line = (
                await asyncio.to_thread(input, f"\n[{state['perm']}/{state['dec']}{ptr_mark}] > ")
            ).strip()
        except (EOFError, KeyboardInterrupt, asyncio.CancelledError):
            print()
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
            print()
        except Exception as e:  # dev-tool: не роняем CLI на сбое хода (коннект к LLM и пр.)
            print(f"\n[ход упал] {type(e).__name__}: {e}")


def run() -> None:
    """Синхронная обёртка для точки входа (`python -m cli` / консольный скрипт `luna`)."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    run()
