---
type: workflow
title: Delegation to subagents
description: How the main agent hands a self-contained sub-task to a subagent with delegate_to_subagent — zone and snapshot, building the subagent, inherited modes, streaming its events, the {summary, artifacts} contract, and failure isolation.
tags: [delegation, subagents, workflow, streaming, isolation]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T17:46:46.267Z
sources:
  - id: openwiki-source-77582883137c489d92067600
    resource: repo://core/agent/tools/_subagents.py
  - id: openwiki-source-277976f7cb0a0aa61d455571
    resource: repo://core/agent/tools/delegation_tools.py
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
---

# Delegation to subagents

Sometimes a request contains a self-contained sub-task that is better done with a fresh, focused
context — research that would otherwise flood the main conversation, a count, a summary, a draft.
The main agent hands it off with **one tool call**, `delegate_to_subagent`. There is no
supervisor or graph edge involved: delegation is just a tool the model chose to call.

## The model of a subagent

A subagent is the same `BaseAgent` class with a different configuration:

- **cold context** — it sees only `task_text` and its own prompt, not the conversation or the
  injected working context;
- **its own prompt** — `agents/<type>/agent.md` from the process procedures folder;
- **its own zone** — `subagents/<uid>/` with `notes/`, `artifacts/` and `.runtime/`, plus a
  read-only view of the main `workspace/`;
- **no history log and no compaction** — it lives for one call;
- **no delegation** — `delegate_to_subagent` is `main_only`, so the hierarchy is one level deep;
- **an advisor, not an editor** — it cannot write into the user's documents; the main agent reads
  its results and decides what to change. That keeps a single writer for the real files.

It runs inside the main agent's turn, in the same process and event loop, not as a separate
process.

## The call

```
delegate_to_subagent(subagent_type: str, task_text: str, task_id: str | None = None)
  → {"summary": str, "artifacts": [str, ...]}            on success
  → {"summary": "...", "artifacts": [], "error": str}     if the subagent crashed
```

`task_id` is an optional label that is echoed in the streamed events.

## Step by step

`delegate_to_subagent` (`core/agent/tools/delegation_tools.py`):

1. **Resolve the type.** If `agents/<subagent_type>/agent.md` does not exist in the procedures
   folder, raise `DelegationError` — a self-correctable error pointing the model to the types
   listed in its prompt. Nothing has been created yet.
2. **Name it.** A fresh `uuid4` hex; the namespace is `subagent.<type>.<first 6 chars>`.
3. **Create the zone** `<session>/subagents/<uid>/{notes,artifacts,.runtime}`. The session root
   is derived from the turn's runtime context (the parent of `workspace/`), not stored anywhere.
4. **Snapshot first.** Take a checkpoint labelled `before_<type>_<uid6>`, so undo can return to
   the state before the subagent's advice was acted on (see
   [checkpoints](../concepts/checkpoints-and-undo.md)).
5. **Announce** a `custom` event `{"type": "namespace_open", "namespace", "subagent_type",
   "task_id"}`.
6. **Build** the subagent with the process `SubagentFactory`: load the prompt, take the pool
   slice for scope `subagent` (no `main_only` tools, no disabled features), set
   `agent_scope="subagent"`, `is_subagent=True`, `subagent_path=<zone>`, and
   `checkpoints_root=None` (the zone is never snapshotted).
7. **Run** `sub.run_stream(task_text, ...)` with:
   - `permission_mode` inherited from the main turn;
   - `session_id` inherited, so the subagent's questions are scoped to the same session and
     answered through the same `/hitl/respond`;
   - `decision_mode="accept_all"` always — the subagent writes only to its own throwaway zone,
     and the real Confirm gate applies later when the main agent edits the user's files.
8. **Forward every event** as a `custom` event `{"type": "subagent_event", "namespace", "mode",
   "data", "task_id"}` through the main graph's stream writer.
9. **Build the result.** `summary` is the subagent's `final_text` (its last `AIMessage`).
   `artifacts` is a sorted listing of every file under the zone's `artifacts/`, as paths
   relative to the session root (`subagents/<uid>/artifacts/count.md`) — taken from the
   filesystem, not from what the subagent claims, so paths cannot be hallucinated.
10. **Close** with `{"type": "namespace_close", "namespace", "task_id", "error"}` in a `finally`
    block, so the UI always sees the frame end.

Event emission is best-effort: if there is no stream writer or writing fails, delegation still
completes.

## Failures

Any `Exception` while building or running the subagent is logged and returned as a normal tool
result with an `error` field and an explanatory `summary`. The main agent's loop continues and
the model decides what to do. A cancelled turn is different: `CancelledError` is not caught, so
cancelling the main turn stops the subagent too (the close event is still emitted).

## What the main agent does with the result

The main prompt describes three separate decisions, none automatic:

1. **Read** the artifact with `read("subagents/<uid>/artifacts/...")` — the `subagents/` prefix
   gives the main agent access to subagent zones.
2. **Author** any change to the user's documents itself, with `edit`/`write`, against the current
   state of the file.
3. **Promote** files worth keeping into `workspace/artifacts/`, because subagent zones are not in
   checkpoints. Subagent zones are not cleaned up automatically; they stay until the session is
   deleted.

## Modes and questions inside a subagent

- `delegate_to_subagent` itself is not a write tool, so it runs in Plan mode and without
  confirmation. In Plan mode the subagent inherits `plan` and cannot write even into its own
  zone.
- A subagent can call `ask_user` / `select_from_options`. Its question travels to the user
  wrapped in a `subagent_event`, and the answer comes back through the session's HITL registry
  (see [modes and HITL](modes-and-hitl.md)). No pending-question marker file is written for
  subagent questions.

## How the front ends show it

The SSE layer normalizes the nested `messages` / `updates` payload inside `subagent_event`, so
subagent tokens and tool calls are readable JSON (see [HTTP API](../interfaces/http-api.md)). The
CLI frames a subagent's output between `namespace_open` and `namespace_close` and indents it (see
[CLI](../interfaces/cli.md)).

## Available subagent types

Only `default-subagent` ships today, and its prompt calls it a technical placeholder for testing
delegation. Adding a type is adding a folder `core/procedures/agents/<type>/agent.md` and listing
it in the main prompt. See [prompts and procedures](../project/prompts-and-procedures.md) and idea
card 006 for the planned `general-purpose` replacement.
