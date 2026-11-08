---
type: interface
title: CLI REPL
description: The luna command-line REPL — one fixed in-process session, how a turn is driven with concurrent answers to agent questions and cancellation, the slash commands, input and rendering.
tags: [cli, repl, interface, prompt-toolkit, rich]
sources:
  - id: openwiki-source-a87578127072d9fd00cf3737
    resource: repo://cli/app.py
  - id: openwiki-source-0999e357957975c8879ee999
    resource: repo://cli/commands.py
  - id: openwiki-source-c46570de609dc2e9a293d8a2
    resource: repo://cli/config.py
  - id: openwiki-source-c51e1c6f2d4bcb4d55b3ef1a
    resource: repo://cli/hitl.py
  - id: openwiki-source-44dfdee2d2532b93ba639c86
    resource: repo://cli/render.py
  - id: openwiki-source-f2e780a6899504a6a25fdf90
    resource: repo://cli/turn.py
generated: { by: "claude-code", at: "2026-10-29T18:35:21.080Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T18:35:21.080Z
---

# CLI REPL

The CLI is an interactive REPL that runs **one session in-process** and drives the agent
directly, with no HTTP and no server. It is the quickest way to use LUNA or to try a change.
Start it with `luna` (console script → `cli.app:run`) or `python -m cli`. It needs a reachable
LLM provider configured in `.env` (see
[LLM providers](../integrations/llm-providers-and-config.md)).

The package is self-contained: it imports the shared build code from `core/agent/bootstrap.py`
and the turn runner from `core/agent/session.py`, the same pieces the HTTP server uses.

## Startup

`cli/app.py` `main()`:

1. creates `~/.luna/cli/` and sends Python logging to `~/.luna/cli/repl.log`, so the terminal only
   shows the rendered turn (real bugs end up in the log with tracebacks);
2. prints the banner with the version read from installed package metadata;
3. calls `bootstrap_process()` → `init_process(~/.luna/cli)` once;
4. builds the session with `new_session()`: the `standalone` agent factory for the fixed folder
   `~/.luna/cli/dev/`, wrapped in an `AgentSession`;
5. loops reading input. A line starting with `/` is a command; anything else is sent as a turn.

Shared mutable state lives in one dict, `cli.config.state`: `perm`, `dec`, `procedures_root` and
`session`. Modules mutate it by key and never reassign it. The CLI starts in **`act` /
`accept_all`** for a frictionless dev loop (the HTTP server starts sessions in `act` /
`confirm`). Before each turn the current modes are copied onto the session.

Because the session folder is reused and not wiped on start, the conversation and workspace
carry over between CLI runs until `/reset` (see
[sessions and storage](../concepts/sessions-and-storage.md)).

## Driving a turn

`drive_turn` (`cli/turn.py`) is the CLI's consumer of the `AgentSession` runner:

1. `session.run_turn(text)` starts the turn as a background task.
2. It drains `session.events()` until a `control` event `turn_done`; `turn_cancelled` prints a
   note.
3. Every event is first checked for a question. If one is found, the key listener and the
   spinner are paused, the CLI asks the user, resolves the question with
   `session.resolve_hitl(...)`, and resumes. This works because the turn runs in its own task:
   while it is parked on a future, the consumer is free to read input.
4. All other events go to the renderer.

If rendering itself throws, the turn is cancelled and its remaining events are drained up to
`turn_done`, so leftovers are never shown as the next turn's output. Any error escaping
`drive_turn` is printed as a short red line and the prompt comes back; a failed turn never kills
the CLI.

### Cancelling

While a turn runs, `CancelKeys` puts the terminal in raw mode and listens for **Esc** or
**Ctrl-C**; either calls `session.cancel_turn()`, which stops the whole turn including a
running subagent. In raw mode Ctrl-C arrives as a key rather than `SIGINT`, so it cancels the
turn instead of killing the process. The listener is removed while a question is open so it
does not swallow the answer. Without a terminal (piped input, tests) it does nothing.

### Answering the agent

`cli/hitl.py` finds question events in `custom` events, unwrapping one level of
`subagent_event` so a subagent's question is handled the same way. The answer has the shape the
tool contract expects:

- **confirm** → a menu *Yes / Yes, and don't ask again / No*, answered with `True`/`False`.
  The middle option switches both the CLI state and the live session to `accept_all`, and later
  confirm requests in the same turn are approved automatically with a dim note.
- **select** → a menu of the options, plus *Your own answer…* when free text is allowed; the result is
  `{"type": "selected", "id": ...}` or `{"type": "free_text", "value": ...}`.
- **ask** → one line of free text.

Ctrl-C or Ctrl-D inside a question cancels the turn. The mechanism behind questions is described
in [modes and HITL](../workflows/modes-and-hitl.md).

## Commands

The list in `cli/commands.py` (`COMMANDS`) feeds both `/help` and autocompletion.

| Command | Effect |
|---|---|
| `/plan`, `/act` | set Permission mode for the next turns |
| `/confirm`, `/accept` | set Decision mode |
| `/attach <path> [--overwrite]` | copy a host file into `workspace/attachments/` through `WorkspaceManager` (same rules and checkpoint as an HTTP upload); the path is the rest of the line, so spaces are allowed |
| `/fs [path]` | print the `workspace/` tree, `.runtime/` hidden |
| `/cp` | list checkpoints with their time |
| `/undo <id>` | restore a checkpoint and reload the agent's history from the restored log |
| `/reset` | wipe the session folder and build a fresh session |
| `/help`, `/?` | list commands and keys |
| `/quit` | exit |

Unknown commands print a hint. Checkpoint semantics are on
[checkpoints and undo](../concepts/checkpoints-and-undo.md).

## Input

`cli/prompt.py` uses prompt_toolkit when stdin and stdout are terminals:

- **Enter** sends; **Alt+Enter** or **Ctrl+J** inserts a newline; pasted multi-line text stays one
  message.
- Completion: `/commands`; host paths after `/attach`; workspace folders after `/fs`;
  checkpoint ids after `/undo`; `@` followed by a workspace file or folder (the walk is capped at
  2000 files).
- **Shift+Tab** toggles Plan/Act.
- **Ctrl+C** at the prompt clears the line; twice within two seconds on an empty line exits.
  **Ctrl+D** exits.
- History is stored in `~/.luna/cli/history`.
- A bottom toolbar shows the modes and the provider/model.

Without a terminal it falls back to plain `input()` and numbered menus.

## Rendering

`cli/render.py` prints with rich into the normal terminal scrollback rather than taking over the
screen. Only the bottom stays live: the still-changing tail of the streamed Markdown answer and a
spinner showing the current activity, elapsed seconds and "esc — interrupt".

- The answer streams as rendered Markdown; reasoning chunks are kept apart from the answer text.
- Output is grouped into two kinds of block, *answer* and *tools*. One blank line is printed under
  the user's prompt and one whenever the turn switches between the two kinds, so the answer is
  visually separated from tool activity without blank lines between consecutive tool calls.
- Each tool call is one line `● name(args)` with the first lines of its result under `⎿`, cut
  with an "… N more lines" count (singular for one line).
- An `edit` call shows a coloured unified diff of `old_string` → `new_string` (up to 40 lines),
  and those two arguments are then not repeated on the call line.
- A subagent's work is framed between `namespace_open` and `namespace_close` and indented with a
  cyan bar.
- `error` events and cancellation notes are printed in place.

The CLI's own texts — menus, notes, help, errors — are in English. The agent's answers, its
tool results and error messages come from the core and the Russian main prompt, so they are
mostly Russian.
