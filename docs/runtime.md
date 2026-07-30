# How a turn works

A "turn" is one round trip: you send a message, the agent works, and you get a reply. This
document follows a turn from start to finish. The code lives in `BaseAgent.run_stream`
([core/agent/base.py](../core/agent/base.py)).

## The think-act loop

The agent does not answer in one shot. It runs a loop:

1. The model reads the conversation so far and decides what to do.
2. If it wants to use a tool, it emits a tool call. LUNA runs the tool and feeds the result back.
3. The model reads that result and decides again — maybe another tool, maybe the final answer.
4. This repeats until the model produces a reply for you instead of a tool call.

So a single request like "change the title in report.json to 'Report 2'" might turn into: look at
the document structure → search for where the title is → apply the edit → tell you it's done.
Four steps, one turn, no plan written in advance — the route is just what the model chose.

There is a hard ceiling (`recursion_limit`, default 50) so a confused model can't loop forever.
If it's hit, the turn ends with a clear error instead of running away.

## No memory in the engine — history is ours

LUNA builds the agent's underlying graph **once** and reuses it. That graph does not remember
anything between turns. Instead, LUNA keeps the conversation itself as a plain list of messages
(`self._messages`) and, for the main agent, mirrors it to an append-only file on disk
(`workspace/.runtime/messages.jsonl`). Every turn, the whole list is handed to the model; every
new message the model or a tool produces is appended to the list and to the file.

This is a deliberate choice: the source of truth is the file on disk, not hidden engine state.
It's what makes undo possible (restore the file and the conversation rewinds with it) and what
lets a fresh server rebuild a session's history by reading the file back.

One thing is deliberately *not* saved: the model's private "reasoning" tokens. They stream to you
live for a nice UI, but they are stripped before anything is written to history — they're
throwaway thinking, not part of the real conversation, and keeping them would just burn context.

## The middleware stack

Every model call and every tool call passes through a small stack of **middleware** — wrappers
that add cross-cutting behavior without touching the agent core. LUNA has four, in this order:

**Tool errors.** When a tool fails, this turns the failure into a normal "error result" message
handed back to the model, instead of crashing the turn. The model reads the error and corrects
itself — a wrong path or a bad argument becomes a retry, not a dead end. Expected failures (like
"file not found") are logged quietly; real bugs are logged with a full trace.

**Permission (Plan / Act).** In **Plan** mode the agent may only read, not write. This middleware
enforces that two ways: it hides the write tools from the model so it isn't even tempted, and — as
a hard backstop — if the model calls a write tool anyway, it blocks the call and returns an error.
In **Act** mode everything is available. See [modes-and-hitl.md](modes-and-hitl.md).

**Decision (Confirm / Accept-all).** In **Confirm** mode, before any tool that writes actually
runs, the turn pauses and asks you to approve it. In **Accept-all** mode writes just run. This is
a gate on *execution*, not a tool the model has to remember to call — so the safety check can't be
skipped by a forgetful model.

**Context injection.** Right before each model call, this quietly appends a small block of live
context to the message list. Two things go in there today: your running notes from
`notes/decisions.md`, and a listing of `workspace/attachments/` — the files you attached — so the
agent knows they're there without going looking for them. The listing carries paths and sizes only;
the agent reads the contents with its tools when the task actually needs them, which matters when
the file is a hundred-megabyte JSON.

That block is a listing of the directory *at that moment*, not a log of what you uploaded. The
difference matters: with a log, deleting a file leaves a notification behind that outlives the file
and the agent goes hunting for something that isn't there. With a listing there is nothing to go
stale — and if the directory is empty, the block says so out loud, which is what corrects the
agent's own earlier replies about a file that used to be there. It's added
as the very last message and wrapped in a clear envelope that says "this is background state, not
a new request from the user." It is never saved to history (it's rebuilt each call from the file),
and it only applies to the main agent — subagents keep their cold context. Appending at the *end*
rather than editing the system prompt is a performance choice: it keeps the model's prompt cache
intact.

## Streaming events back

As the turn runs, the agent emits a stream of events so the UI (or CLI) can show progress live
instead of waiting for the end. Each event is tagged with the agent it came from (`main`, or
`subagent.<type>.<id>` for a delegated task) and carries one of a few kinds of payload:

- **messages** — the model's reply, token by token, as it's generated.
- **updates** — a tool was called or a tool returned a result.
- **custom** — domain events from tools: a subagent starting or finishing, a JSON patch being
  applied (used for live document preview), and so on.

The CLI colors these as it prints them; the web UI renders `main` as the chat and `subagent.*` as
progress cards under the delegation. The HTTP server forwards the same stream to the browser as
Server-Sent Events (see [interfaces.md](interfaces.md)).

## Keeping long chats in the window

A long conversation would eventually overflow the model's context window. LUNA handles this with
**compaction** (`HistoryCompactor`, [core/agent/compaction.py](../core/agent/compaction.py)).

Before a turn runs, if the history has grown past a token threshold (about 70k), LUNA summarizes
the older part into a short text and replaces it with that summary, keeping roughly the last 30
messages intact. The working list becomes `[summary, ...recent messages]`. It summarizes **once**
and reuses the result — it does not re-summarize every turn.

Two details make it safe: the raw log on disk is never shortened (only the in-memory working copy
is compacted, and the summary is recorded with a pointer into the log), and the cut is never made
in the middle of a tool call and its result — that pair always stays together, or the model would
choke on an orphaned result. Compaction runs only for the main agent; subagents are short-lived
and don't need it.

## Checkpoints and undo

Every time a write tool succeeds, LUNA takes a **checkpoint**: a full copy of the `workspace/`
folder, saved under `checkpoints/`. Delegating to a subagent also takes one first, so you can undo
back to the state *before* the subagent ran.

Because the snapshot includes the history file (`.runtime/messages.jsonl`), restoring a checkpoint
rewinds both the documents **and** the conversation to that moment. After a restore, the agent
reloads its in-memory history from the restored file so it doesn't keep talking as if the undo
never happened.

This is a filesystem mechanism, not an engine feature — a checkpoint is literally a folder copy.
The details, including a known performance cost, are in [storage.md](storage.md) and
[tech-debt.md](tech-debt.md) (TD-15).

## When a turn fails

If the LLM backend is unreachable or the model isn't loaded, the turn ends with a friendly
"model unavailable" event rather than a stack trace. Other unexpected errors are logged with a
full trace and surfaced to whoever is consuming the stream. In the CLI, a failed turn prints a
short `[turn failed]` line and hands the prompt back — it never kills the session. You can also
cancel a running turn yourself (`/stop` over HTTP, Ctrl-C in the CLI); cancellation tears down the
whole turn, subagent included.

## Next

- The disk layout behind history and checkpoints: [storage.md](storage.md)
- What the tools in the loop actually do: [tools.md](tools.md)
- The Plan/Act and Confirm switches: [modes-and-hitl.md](modes-and-hitl.md)
