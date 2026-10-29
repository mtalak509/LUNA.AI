---
type: subsystem
title: Middleware stack
description: The four LangChain middleware LUNA wraps around every model and tool call — tool-error conversion, Plan/Act permission, Confirm gate, and working-context injection — their order and exact behaviour.
tags: [middleware, runtime, permissions, context, langchain]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T17:46:46.267Z
sources:
  - id: openwiki-source-364a89a77ff1b7418dc1c6c3
    resource: repo://core/agent/base.py
  - id: openwiki-source-dd85edee451d1cd631927428
    resource: repo://core/agent/middleware/context_inject.py
  - id: openwiki-source-d23fb6387169d3d01a2355f0
    resource: repo://core/agent/middleware/decision.py
  - id: openwiki-source-c1be0e08be41c4473bf84742
    resource: repo://core/agent/middleware/permission.py
  - id: openwiki-source-2b29142abb8c5acbac285b63
    resource: repo://core/agent/middleware/tool_error.py
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
---

# Middleware stack

Cross-cutting behaviour in LUNA is not written into the agent loop. It is added as LangChain
middleware that wraps the two calls the loop makes over and over: a **model call** and a
**tool call**. `BaseAgent._build_middleware` (`core/agent/base.py`) returns the same list for
every agent, main or subagent:

```python
[ToolErrorMiddleware(), PermissionMiddleware(), DecisionMiddleware(), ContextInjectMiddleware()]
```

All four read the per-turn `AgentRuntimeContext` through `request.runtime.context`, so the same
middleware instances behave differently per turn and per agent.

## Order matters

LangChain treats the first middleware in the list as the outermost wrapper. For a tool call
the chain is therefore:

```
ToolError → Permission (gate) → Decision (confirm) → the tool itself
```

and for a model call:

```
Permission (hide write tools) → ContextInject (append context) → the model
```

Two consequences follow. `ToolErrorMiddleware` sits outside everything, so an exception raised
by a tool *or* by an inner middleware still reaches the model as an error result. And the Plan
gate runs before the Confirm gate, so in Plan mode a write call is refused outright instead of
asking the user to approve something that would be refused anyway.

## ToolErrorMiddleware — failures become feedback

`create_agent`'s default tool error handling only returns argument-validation errors to the
model and re-raises everything else, which would kill the turn. `ToolErrorMiddleware` restores
the contract LUNA relies on: **a failing tool is a message, not a crash**. Any `Exception` from
the wrapped call is turned into a `ToolMessage(status="error")` with the text
`Tool error: <name> raised <Type>: <message>`.

It distinguishes two kinds of failure only for logging:

- a `ToolException` (file not found, path outside the zone, ambiguous `edit`, unknown subagent
  type…) is an expected outcome and is logged at debug level without a traceback;
- any other exception is treated as a bug and logged as a warning with a traceback.

Every domain error class in the tools (`FileAccessError`, `DelegationError`, `HitlError`,
`RagError`, …) subclasses `ToolException` for exactly this reason. `asyncio.CancelledError` is
not an `Exception`, so cancelling a turn passes straight through.

## PermissionMiddleware — Plan / Act

In `plan` mode the agent may only read. The middleware enforces that in two layers, both
evaluated per turn because the graph and its tool list are built once:

- **Hide** (`awrap_model_call`): write tools — those whose metadata has `is_write=True` — are
  removed from `request.tools`, so the model does not even see them.
- **Gate** (`awrap_tool_call`): if the model calls a write tool anyway (it may have seen the
  name in its prompt), the call is short-circuited into an error `ToolMessage` saying the tool
  is unavailable in Plan mode and was not executed.

In `act` mode both layers pass everything through. The same rules apply to subagents, whose
permission mode is inherited from the main agent's turn.

## DecisionMiddleware — Confirm / Accept-all

This is a gate on **execution** of write tools, not a tool the model must remember to call. An
earlier design used a separate confirm tool and the model sometimes skipped it.

- Non-write tools always pass.
- In `accept_all` mode write tools pass.
- In `confirm` mode the call parks on `request_human(kind="confirm", ...)` with an
  `action_description` built from the tool name and its arguments. A false answer returns an
  error `ToolMessage` ("the user declined the action"); a true answer runs the tool.

The pending-question marker is written only when the main agent asks. Subagents always run with
`decision_mode="accept_all"` (they write only into their own zone), so this middleware is
effectively a no-op for them. The full HITL mechanism is in
[modes and HITL](../workflows/modes-and-hitl.md).

## ContextInjectMiddleware — live working context

Before every model call of the **main** agent (`agent_scope == "main"`; subagents keep a cold
context), the middleware asks a list of *context providers* for blocks of text and appends them
as one extra `HumanMessage` at the very end of the request:

```
<working_context>
(envelope: this is auto-generated state, not a user request; don't answer it;
 prefer it over stale history; system instructions and the current request still win)

<project_decisions>…</project_decisions>
<attachments>…</attachments>
</working_context>
```

Sections are XML tags, the same convention the agent prompts use. Two providers exist today:

- **`FileContextProvider`** (`project_decisions`) — the stripped contents of
  `workspace/notes/decisions.md`, or nothing if the file is missing or empty.
- **`AttachmentsContextProvider`** (`attachments`) — a listing (path and size) of
  `workspace/attachments/` *at this moment*. If the directory was never created, there is no
  section. If it exists but is empty, the section says so explicitly and adds that any
  previously mentioned attachments are gone. Silence would not correct the model's own earlier
  claims; an explicit "empty" does.

Design points worth knowing:

- The block is **appended at the tail** instead of edited into the system prompt, which keeps
  the provider's prompt cache valid across calls.
- It lives only inside the model request (`request.override`), so it is **never persisted** to
  `messages.jsonl` and is rebuilt from disk on every call.
- Adding a context source means adding a provider (an object with `tag` and an async
  `block(ctx)`), not touching the stack. Providers can be passed to the constructor.

Attachments as a directory listing rather than an upload log is a deliberate choice explained
in [zones and attachments](../concepts/zones-and-path-scope.md).
