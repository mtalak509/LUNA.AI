---
type: runtime flow
title: A turn inside BaseAgent
description: How BaseAgent.run_stream runs one turn of the think-act loop — history ownership without a checkpointer, compaction, streaming modes, what gets persisted, and how errors end a turn.
tags: [runtime, agent, streaming, history, langgraph]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T17:46:46.267Z
sources:
  - id: openwiki-source-364a89a77ff1b7418dc1c6c3
    resource: repo://core/agent/base.py
  - id: openwiki-source-07e76361ddee296c51381ff5
    resource: repo://core/agent/session.py
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
---

# A turn inside BaseAgent

A *turn* is one round trip: a user message goes in, the agent calls tools as many times as it
wants, and a reply comes out. All of it happens in one method, `BaseAgent.run_stream`
(`core/agent/base.py`). The same class runs the main agent and every subagent; the only
difference is the `AgentConfig` it was built with.

## The graph is built once and remembers nothing

`BaseAgent.__init__` calls LangChain's `create_agent` exactly once, with the chat model, the
agent's tool slice, its system prompt, the middleware stack and `AgentRuntimeContext` as the
context schema. No checkpointer is passed, so the compiled graph is stateless between turns.

The conversation is owned by LUNA instead. `self._messages` is a plain Python list, and on every
turn the *whole* list is handed to `astream`. For the main agent the list is loaded from a
`MessageStore` at construction time and mirrored back to it as the turn runs; a subagent gets no
store and starts with an empty list (see
[history and compaction](history-and-compaction.md)).

## Step by step

1. **Build the per-turn context.** `run_stream` creates an `AgentRuntimeContext` with the
   current `permission_mode`, `decision_mode`, the agent's namespace, workspace path, scope,
   `session_id`, checkpoints root and subagent zone. Middleware and tools read it through
   `request.runtime.context` or `get_runtime().context`. Because modes travel in this object,
   a mode change applies from the next turn without rebuilding the graph.
2. **Record the user message.** The text becomes a `HumanMessage`, is appended to
   `self._messages`, and (main agent only) appended to the store.
3. **Compact if needed.** If a `HistoryCompactor` exists, `maybe_compact` may replace the list
   with `[summary, *tail]` before the graph sees it.
4. **Run the graph.** `astream` is called with `stream_mode=["messages", "updates", "custom"]`
   and `recursion_limit` (default `DEFAULT_RECURSION_LIMIT = 50`). Every chunk is re-emitted as a
   `StreamEvent(namespace, mode, data)`.
5. **Persist only real messages.** For `updates` chunks coming from the `model` or `tools` node,
   the produced messages are passed through `_strip_reasoning`, appended to `self._messages`
   and, for the main agent, to the store. Token chunks (`messages` mode) are streamed out but
   never stored — they are partial deltas for the UI.

Counters for model steps and tool calls are kept only for the `turn start` / `turn done` log
lines, which go through a log adapter that prefixes every line with the agent's namespace.

## Stream events

`StreamEvent` wraps a LangGraph chunk and adds the namespace of the agent that produced it:
`main` for the main agent, `subagent.<type>.<uid6>` for a delegated one. Three modes come
straight from LangGraph:

- `messages` — model token deltas (and some non-delta messages LangGraph emits there);
- `updates` — node outputs: tool calls requested by the model, tool results;
- `custom` — dict events written by tools through `get_stream_writer()`: subagent lifecycle,
  HITL questions.

Two more modes are produced by LUNA itself: `error` (from `run_stream`'s own failure handling)
and `control` (from the `AgentSession` runner: `turn_cancelled`, `turn_done`). The HTTP layer
turns all of them into SSE frames (see [HTTP API](../interfaces/http-api.md)).

## Reasoning is streamed but never kept

Reasoning blocks are shown live, but `_strip_reasoning` removes content blocks of type
`reasoning` before a message is appended to the working copy or the log. Messages that had
reasoning are copied with `model_copy`, so tool calls, ids and usage metadata survive. The
reasoning of past steps is throwaway thinking; sending it back on later turns would only burn
context. The provider-side patch that makes vLLM reasoning visible in the first place is
described in [LLM providers](../integrations/llm-providers-and-config.md).

## How a turn ends badly

- **Recursion limit.** A `GraphRecursionError` is logged and turned into an `error` event with
  `{"error": "recursion limit exceeded", "limit": ...}`; the generator then finishes normally.
- **Model unavailable.** `_is_model_unavailable_error` treats an `openai.APIConnectionError`,
  or a 404 whose message mentions "model not found" / "no running instances available", as a
  temporarily missing backend. The turn yields an `error` event with
  `code: "model_unavailable"` and stops.
- **Anything else** is logged with a traceback and re-raised. The `AgentSession` runner catches
  it and emits an `error` event with the exception text, followed by `turn_done`.
- **Cancellation** comes from outside: `AgentSession.cancel_turn` cancels the task running the
  turn, `CancelledError` unwinds through `run_stream`, and the runner emits `turn_cancelled`
  and `turn_done`. Because a subagent runs inside the delegation tool of the same task, the
  whole tree stops together.

Tool failures are not turn failures: the middleware converts them into error `ToolMessage`s
the model can read (see [middleware](middleware.md)).

## After the turn

Two small accessors exist so callers never touch `_messages` directly:

- `final_text` scans backwards for the last `AIMessage` and returns its text. The delegation
  tool uses it as a subagent's `summary`.
- `reload_history` reloads the working copy from the store. It is called after a checkpoint
  restore replaced `messages.jsonl` on disk, so the agent does not keep talking from the
  "future". Subagents have no store, so it is a no-op for them.

The summarizer used by compaction is also built here (`_make_summarizer`): one extra model call
with a fixed Russian summary prompt over the rendered head of the history.

## Who drives `run_stream`

Nobody consumes `run_stream` directly in production. `AgentSession.run_turn`
(`core/agent/session.py`) starts it as an `asyncio.Task` and pushes every event into a queue,
which the HTTP SSE route and the CLI both drain. That split is what lets a turn park on a
question while the consumer stays free to answer it. Only one turn per session may run at a
time: a second `run_turn` while busy raises `RuntimeError`, because concurrent turns would
interleave writes into the same shared message list. See
[sessions and storage](../concepts/sessions-and-storage.md).
