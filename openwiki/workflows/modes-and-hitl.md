---
type: workflow
title: Permission and decision modes, human-in-the-loop
description: The two per-session safety switches (Plan/Act and Confirm/Accept-all) and the mechanism by which a turn parks on a question to the user and resumes when the answer arrives over HTTP or in the CLI.
tags: [modes, hitl, permissions, confirmation, workflow]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T17:46:46.267Z
sources:
  - id: openwiki-source-07e76361ddee296c51381ff5
    resource: repo://core/agent/session.py
  - id: openwiki-source-23ea0cffc72ce040b46eb998
    resource: repo://core/agent/tools/_hitl.py
  - id: openwiki-source-ae7f2a24717c9d8b001d8c1c
    resource: repo://core/agent/tools/hitl_tools.py
  - id: openwiki-source-3f9d7ad731f632978576bec7
    resource: repo://core/app/routes/sessions.py
  - id: openwiki-source-4a9e014667c5d2fd5ea34178
    resource: repo://core/app/routes/turn.py
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
---

# Permission and decision modes, human-in-the-loop

LUNA keeps the human in control in two ways: two switches that limit what the agent may do, and
a way for a running turn to stop and ask the user something. Both are built on the same
mechanism — a tool call that **parks on an `asyncio.Future`** until an answer arrives from
outside the graph.

## Two independent switches

| Switch | Values | Effect |
|---|---|---|
| Permission | `plan` / `act` | `plan` = read-only: write tools are hidden from the model and blocked if called anyway |
| Decision | `confirm` / `accept_all` | `confirm` = every write tool call waits for the user's approval |

All four combinations are valid. "Write tool" means a tool declared with `is_write=True`
(`write`, `edit`, `rm`). Enforcement lives in middleware at the execution boundary, so it does
not depend on the model cooperating; an earlier design with a separate "confirm" tool failed
because models sometimes skipped it. Details on [middleware](../architecture/middleware.md).

**Defaults.** The HTTP server creates sessions in `act` / `confirm`; the CLI starts in `act` /
`accept_all` for a frictionless dev loop. The eval harness uses `act` / `accept_all`.

**Changing them.** Modes are attributes of the `AgentSession` runner, copied into the runtime
context when a turn starts. A change made while a turn runs — `POST /sessions/{id}/mode`, or
`/plan`, `/act`, `/confirm`, `/accept`, Shift+Tab in the CLI — applies from the **next** turn. The
one exception is the CLI's "yes, and don't ask again" answer, which also auto-approves the rest
of the current turn's confirmations on the CLI side.

**Subagents** inherit the main turn's permission mode but always run with `accept_all`, since
they can only write into their own throwaway zone (see
[delegation](delegation-and-subagents.md)).

## Questions from the agent

Two tools let the model ask deliberately (`core/agent/tools/hitl_tools.py`). Both are read-only,
available to main agent and subagents.

**`ask_user(question, context_for_main?, allow_main_dismissal=True)`** — an open question with a
free-text answer, for missing information, a real fork in meaning, or ambiguous results. It
returns `{"value": <answer>, "source": "user"}`.

**`select_from_options(question, options, source, allow_free_text=True,
allow_main_dismissal=True)`** — a choice among 1 to 10 options, each `{"id", "label",
"metadata"?}`. The options must come from an earlier tool result (the docstring tells the model
not to invent them) and `source` names that tool. Validation happens before parking: empty lists,
more than 10 options and malformed options are `HitlToolsError`s the model can fix. The answer is
`{"type": "selected", "id", "source"}` or, if free text is allowed and used,
`{"type": "free_text", "value", "source"}`.

`context_for_main` and `allow_main_dismissal` exist for a designed but not implemented path in
which the main agent could answer a subagent's question itself. Today every question is forwarded
to the user, and the answer is always tagged `source: "user"`.

## The parking mechanism

`request_human` (`core/agent/tools/_hitl.py`) is shared by both tools and by the Confirm gate:

1. **Register** a new `hitl_id` (uuid hex) and a future in the process-wide `HitlRegistry`,
   tagged with the current `session_id`.
2. **Write a marker** `workspace/.runtime/hitl_pending.json` with the id, kind, payload and time —
   only when the main agent asks. Nothing in the code reads it back yet; it records that a
   question was open if the process died.
3. **Emit** a `custom` event through the stream writer: `hitl_confirm` for confirmations,
   `hitl_question` for `ask` and `select`, carrying `hitl_id`, `namespace`, `kind` and the
   payload fields (question, options, action description…).
4. **Await** the future. The turn is suspended at that exact point; nothing is torn down or
   replayed.
5. **Clean up** in `finally`: drop the registry entry and delete the marker, whether the answer
   came, the turn was cancelled, or an error occurred.

This is LUNA's replacement for LangGraph `interrupt`/resume, which would need a checkpointer and
would re-run the interrupted tool node on resume — unsafe for tools with side effects.

### Answering

`AgentSession.resolve_hitl(hitl_id, value)` calls `HitlRegistry.resolve(hitl_id, value,
session_id)`. Resolving is scoped to the session: an id that does not exist **or** belongs to
another session raises the same `HitlError("Unknown hitl_id")`, so a session cannot learn about
another session's questions, and the entry is left intact for its real owner.

- **HTTP:** `POST /sessions/{id}/hitl/respond` with `{"hitl_id", "value"}`; `404` on
  `HitlError`. While waiting, the SSE stream sends a `: ping` every 30 seconds (see
  [HTTP API](../interfaces/http-api.md)).
- **CLI:** `drive_turn` spots the event, pauses the spinner and cancel keys, asks in place and
  resolves (see [CLI](../interfaces/cli.md)).

The `value` shape follows the tool: a string for `ask`, a boolean for `confirm`, an object with
`type` for `select`. `GET /health` reports how many questions are pending across all sessions.

### Questions from a subagent

A subagent never talks to the user directly. Its question event is wrapped by the delegation
tool in a `subagent_event`, so it reaches the user through the main stream with the subagent's
namespace. Because the subagent inherited the main turn's `session_id`, the same
`/hitl/respond` (or CLI prompt) resolves it, and the answer flows back down to the parked
subagent tool call.

### Cancellation

Cancelling a turn (`POST .../stop`, Esc/Ctrl-C in the CLI) cancels the task that is awaiting the
future. The `finally` block removes the registry entry and marker, so no stale question is left
behind.
