---
type: subsystem
title: Conversation history and compaction
description: How the main agent's conversation is kept as an append-only messages.jsonl log with summary markers, and how HistoryCompactor shrinks long histories once without orphaning tool results.
tags: [history, compaction, persistence, messages, runtime]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T17:46:46.267Z
sources:
  - id: openwiki-source-364a89a77ff1b7418dc1c6c3
    resource: repo://core/agent/base.py
  - id: openwiki-source-6c1dca47c3baeab5649535ab
    resource: repo://core/agent/compaction.py
  - id: openwiki-source-30add05b9a24cb76dcc6329a
    resource: repo://core/agent/messages.py
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
---

# Conversation history and compaction

LUNA runs its agent graph without a LangGraph checkpointer, so the conversation has to live
somewhere else. It lives in two places that are kept in step: an in-memory working copy
(`BaseAgent._messages`) that is fed to the model every turn, and a durable append-only log on
disk that the working copy can always be rebuilt from. Only the main agent has the log; a
subagent is short-lived and keeps its history in memory only.

## The log: `MessageStore`

`MessageStore` (`core/agent/messages.py`) owns `workspace/.runtime/messages.jsonl` for one
session. Each line is one JSON record of one of two kinds:

- **`message`** — one LangChain message serialized with `messages_to_dict`. `append` writes
  these as the turn produces them.
- **`summary`** — a compaction marker: the summary message plus `head`, the index (counted over
  `message` records only) where the retained tail of the conversation starts. `append_summary`
  writes these.

The file is never rewritten. Compaction adds a marker; it does not delete old lines, so the raw
conversation is always there for audit and undo.

### Loading

`load` rebuilds the working copy. Without any summary marker it returns every message. With
markers it takes the **last** one and returns `[summary, *messages[head:]]`. It also recounts
`message_count`, which the compactor needs to compute the next `head`. `load` is called when the
agent is constructed and again after a checkpoint restore.

`load_ui_messages` is a separate read path for the web UI (`GET /sessions/{id}/history`). It
returns raw dicts without rebuilding LangChain objects, in the shape
`{"summary": ..., "messages": [...]}`, and it tolerates a half-written **last** line (the log is
read while a live turn may still be appending). A broken line in the middle of the file is still
an error, because that means corruption rather than a race.

### Large strings go to blobs

Before a message is written, `_externalize` walks it and replaces any `data:` URI longer than
8192 characters with a `blob:<sha1>` reference, storing the string in
`.runtime/blobs/<sha1>.b64`. This keeps the log small when a message carries base64 media. The
reference is not inflated back on load: the log is for undo and audit, not for re-sending media.

## Compaction: `HistoryCompactor`

A long session would eventually overflow the model's context window. `HistoryCompactor`
(`core/agent/compaction.py`) is LUNA's answer, used instead of LangChain's built-in summarization
middleware. The built-in one runs inside the graph; because LUNA replays the full history each
turn, it would summarize again on every turn. LUNA's compactor runs at the turn boundary, before
the graph, and summarizes **once**.

`BaseAgent.run_stream` calls `maybe_compact` right after appending the user's message:

1. If the approximate token count of the working copy (`count_tokens_approximately`) is below
   `trigger_tokens` (70,000 by default), nothing happens.
2. Otherwise it picks a split point that keeps the last `keep_messages` (30 by default).
3. The head is summarized by an injected `Summarizer` — in production one extra call to the same
   chat model with a short Russian instruction (`BaseAgent._make_summarizer`).
4. The summary becomes a `HumanMessage` prefixed with `[Сводка предыдущего диалога]`, is written
   to the log with `append_summary(head=message_count - len(tail))`, and the working copy becomes
   `[summary, *tail]`.

The next turns start from that already-summarized list, so the summary is reused until the
history grows past the threshold again.

### Never orphan a tool result

The split point is chosen by `_safe_split_index`. If the first message of the tail would be a
`ToolMessage`, the split moves backwards until it is not. That keeps every `AIMessage` with
`tool_calls` and its `ToolMessage` results on the same side of the cut. A tool result whose call
was summarized away would make the provider reject the request.

### Why `head` is counted from the log, not the list

After a previous compaction, the first element of the working copy is a summary that is not a
`message` record in the log. Counting `head` from `len(messages)` would therefore be off by the
summaries. The compactor uses `MessageStore.message_count` instead, which counts only real
message records.

## What is not stored

- **Token deltas** from the `messages` stream mode are never written; only completed messages
  from `updates` are.
- **Reasoning blocks** are stripped before writing (see [a turn inside BaseAgent](agent-turn.md)).
- **The working-context envelope** injected by `ContextInjectMiddleware` exists only inside the
  model request and is rebuilt on every call (see [middleware](middleware.md)).

## Relation to undo

`messages.jsonl` lives inside `workspace/`, so every checkpoint snapshot carries the history at
that moment. Restoring a checkpoint therefore rewinds files and conversation together, after
which the agent calls `reload_history` to drop its stale in-memory copy. See
[checkpoints and undo](../concepts/checkpoints-and-undo.md).
