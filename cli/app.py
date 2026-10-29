"""CLI entry point: a REPL loop over the AgentSession runner.

A turn runs as an asyncio.Task plus an event queue, and the stream is printed with rich
(`cli/render.py`). Input is prompt_toolkit (`cli/prompt.py`): history, `/commands` and
`@files` with completion, and a status line. The runner separates the turn from the
consumer, so the CLI can answer HITL and interrupt a turn: on `hitl_*` it reads the
answer and calls `session.resolve_hitl(...)`, on esc/ctrl+c it calls
`session.cancel_turn()`. No HTTP server; a running LLM provider (.env) is required.

Run:     python -m cli   (or the `luna` console script)
Commands: /plan /act  /confirm /accept  /attach <path>  /fs [path]  /cp  /undo <id>
          /reset  /help  /quit
Keys:    esc/ctrl+c — interrupt the turn; shift+tab — plan/act; alt+enter — new line;
         ctrl+c twice on an empty line, or ctrl+d — quit.
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

EXIT_WINDOW = 2.0  # seconds between two ctrl+c on an empty line to quit


async def main() -> None:
    # Logs go to a file, not the console: the CLI shows only the render, and traces of
    # real bugs land in repl.log (ordinary tool failures stay quiet debug; see
    # ToolErrorMiddleware). mkdir before basicConfig: the file handler opens the log
    # immediately.
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, filename=SESSION_ROOT / "repl.log", encoding="utf-8")

    console.print(Text(print_banner(), style="yellow"))

    # Explicit init/build pair — the process once, the session separately (as in server.py).
    bootstrap_process()  # init_process → state["procedures_root"]
    state["session"] = new_session()  # creates workspace/ and a runner over MainAgent
    console.print(
        Text.assemble(
            f"CLI ready (session={SESSION_DIR}). ",
            ("/help — commands, /quit — quit.", "dim"),
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
            console.print(Text("ctrl+c again — quit", style="dim"))
            continue
        except (EOFError, asyncio.CancelledError):
            break

        if not line:
            continue

        if line.startswith("/"):
            if not handle_command(line):
                break
            continue

        # A slash command may have changed the modes — pass the current ones into this turn.
        session = state["session"]
        session.permission_mode = state["perm"]
        session.decision_mode = state["dec"]
        try:
            await drive_turn(session, line)
            console.print()
        except Exception as e:  # a failed turn must not kill the CLI
            console.print(Text(f"\n[turn failed] {type(e).__name__}: {e}", style="bold red"))


def run() -> None:
    """Synchronous wrapper for the entry point (`python -m cli` / the `luna` script)."""
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()


if __name__ == "__main__":
    run()
