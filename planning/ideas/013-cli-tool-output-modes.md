# 013. Choose how much tool activity the CLI shows

- id: 013
- status: shaped
- source: own
- value: high

## Why

The terminal renderer already shortens tool arguments and results, but the user cannot
choose the level of detail. A normal conversation needs a compact view; debugging a tool
needs the complete available arguments and result; reading the answer sometimes needs
no tool blocks at all.

Use the same `--tools=compact|full|off` choice as Nika's terminal client, adapted to
LUNA's existing event stream and renderer.

## What changes

Add a CLI startup option `--tools=compact|full|off`, with `compact` as the default:

- `compact` keeps the current short call line, result preview and bounded edit diff.
  Any shortened content is clearly marked. Preview sizes stay in the renderer.
- `full` expands the available tool arguments, results and tool diff previews without
  the compact renderer's cuts. It does not bypass masking or restore content already
  truncated by the core or the tool-output decorator in [002](002-truncate-tool-output.md).
- `off` hides routine tool call/result blocks and their diff previews. Model answers,
  turn errors, permission requests and HITL confirmation/select remain visible;
  information needed to approve an action must still be shown.

Apply the setting consistently to the main agent and subagent tool activity, while
preserving call/result correlation and normal terminal scrollback. Treat tool data as
plain text so Rich markup and terminal control sequences cannot execute through it.

The option controls presentation only. The CLI continues consuming every `StreamEvent`;
it does not disable tools, alter model context, change permissions, approve actions or
cancel execution. Changing the display policy belongs in `cli/render.py`, with the
startup option passed through the existing CLI/turn setup. This extends
[004](004-cli-prompt-toolkit-rich.md), without changing the core event contract.

Check the three modes against the same deterministic event sequence: a tool call with
long arguments/result, an edit, subagent activity, a tool failure and a confirmation.
Only the amount of routine tool output should change; the answer and required user
decisions must remain the same.

## Why this is LUNA

`AgentSession` already emits events independently of its consumers. The terminal can
choose how to present those events without adding another execution path, server or
storage layer. This is a small extension of the existing prompt_toolkit/Rich CLI.

## Out of scope

- Implementing the option as part of this idea card.
- Changing `AgentSession`, `StreamEvent`, tool execution or the web UI.
- Changing model-context truncation or spill files from card 002.
- A new non-interactive mode, JSONL export, saved diagnostic replay or a diagnostics API.
- Interactive switching commands, per-tool settings or a broader terminal redesign.
