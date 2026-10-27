# 004. CLI on prompt_toolkit and rich

- id: 004
- status: accepted
- source: own
- value: high

## Why

The CLI is a bare REPL. Input is `input()` in a thread: no history, no multi-line input, no
completion. Tool calls print raw args and the full result, so a large JSON floods the screen.
Model text is not rendered as markdown. Ctrl+C closes the REPL instead of stopping the turn.
HITL select asks for a number. Modes are visible only in the prompt.

## What changes

The CLI keeps printing into normal terminal scrollback, like Claude Code. Only the current
block, the input line and the status line are live. No full-screen app.

- `prompt_toolkit` owns input: persistent history in `~/.luna/`, multi-line input, completion
  for slash commands and `@` paths in the session workspace, key bindings, and a bottom
  toolbar with modes, model and context usage.
- `rich` owns output: streamed markdown for model text, one line per tool call with a short
  result under it, long results collapsed, subagent work in an indented block, a spinner
  while the model is silent, and a colored diff for edits.
- Esc or Ctrl+C cancels the running turn through `AgentSession.cancel_turn`. A second Ctrl+C
  exits.
- HITL confirm and select become arrow-key menus. Free text stays available.
- `render.py` becomes a stateful view over `StreamEvent`. `turn.py` and `hitl.py` keep their
  shape. The agent core does not change.

Order: input, then rendering, then cancellation, then HITL menus and diffs.

## Why this is LUNA

The core already separates the turn from its consumer: `AgentSession` emits events and the
CLI only renders them. The new CLI is a better consumer of the same contract. Both libraries
are pure Python and work in native Windows terminals.

## Out of scope

- A full-screen TUI (Textual) with panels. Possible later on top of `rich`.
- A frontend in another language (Ink, Bubble Tea, Ratatui).
- Changes to `AgentSession`, `StreamEvent` or the HTTP server.
- Resume of past sessions and a non-interactive `luna -p` mode. Separate cards if needed.
