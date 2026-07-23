# Architecture

This is the map of the whole system. Read it first; the other documents zoom into one piece each.

## What LUNA is

LUNA helps you work with documents and files by talking to a language model. You write a request
in plain language; the model figures out the steps and carries them out by calling **tools** —
small functions that read a file, search inside a big JSON document, apply an edit, and so on.

The important design choice is that LUNA is a **thin harness around one agent**, not a workflow
system. There is no diagram of boxes and arrows that decides "first classify the request, then
route it here, then there." The model itself decides the next step every time, and the route
through the work simply emerges as the sequence of tools it chooses to call. LUNA's job is to
give that model a good set of tools, a safe place to work, and a way to stream progress back to
you.

There is **no database and no separate orchestrator process**. The agent server is the entire
backend, and everything the agent remembers lives as files on disk.

## Three ideas the design rests on

Everything else follows from these three.

**1. The agent drives itself.** No router, no intent classifier, no supervisor deciding who
handles what. The main agent reads your message and picks its own next tool call. This keeps the
system simple and lets the model handle requests nobody planned for in advance.

**2. Tools are shared; specialization is just a prompt.** There is one flat set of tools. Every
agent draws from the same set. When LUNA needs a "specialist" for a subtask, that specialist is
*not* a new class with its own private tools — it is the same agent code started with a different
instruction file. What makes it a specialist is its prompt, not its wiring.

**3. State lives in files.** Anything worth remembering — the documents, your notes, the chat
history, the undo snapshots — is written to disk, not held only in memory. Because of this, any
step can be interrupted and picked up again, and "undo" is just restoring a folder.

## The main agent

When you open a session you get **one main agent**. It is the only thing you talk to. It runs the
conversation from start to finish, makes every decision, and writes every reply. It has the full
set of tools available.

Internally the agent runs a **think-act loop** (often called ReAct): the model thinks, calls a
tool, sees the result, thinks again, calls another tool, and so on until it has an answer for
you. [runtime.md](runtime.md) covers exactly how one turn of this loop works.

The main agent is built on top of LangChain's `create_agent`. The class is `BaseAgent`
([core/agent/base.py](../core/agent/base.py)). The same class is used for subagents too — the
only difference is the configuration they start with.

## Subagents

Sometimes a request has a self-contained sub-task that is better handled with a fresh, focused
context — for example, a research task that would otherwise flood the main conversation with
details. For that, the main agent can call one tool, `delegate_to_subagent`, and hand the
sub-task off.

A subagent is the same `BaseAgent` code, started with:

- a **cold context** — it sees only the task text it was given, not the main conversation or the
  documents;
- its **own instruction file** (`agents/<type>/agent.md`) that makes it a "specialist";
- its **own scratch folder** to work in.

A subagent is an **advisor, not an editor**. It investigates, writes its findings into its own
folder, and returns two things: a short `summary` and a list of `artifacts` (the files it
produced). It never edits the main documents itself. The main agent reads the advice and decides
what, if anything, to change. This keeps a single writer for the real documents and avoids two
agents stepping on each other.

Subagents are one level deep (a subagent cannot delegate further), they never talk to you
directly, and they run **inside the same process** as the main agent — as an `asyncio` task, not
a separate program. When the main agent delegates, it launches that task, forwards the subagent's
progress events into its own stream (tagged so the UI can show them separately), and waits for
the result. If a subagent crashes, the error comes back as a normal failed tool result; the main
loop keeps going.

## One process, many sessions

The backend is a single FastAPI server ([core/app/server.py](../core/app/server.py)). It can hold
**many sessions at once**. A session is one conversation: it owns a folder on disk
(`~/.luna/sessions/<id>/`) and one main agent instance. A `SessionManager` keeps the registry of
live sessions in memory and the front end talks to them through `/sessions/{id}/...` routes.

Two things to keep in mind:

- Sessions live **only in memory plus on disk within the running process**. If the server
  restarts, the in-memory registry is gone, and on startup LUNA wipes leftover session folders so
  no orphans pile up. (Checkpoints and history survive *within* a session's lifetime, not across
  a server restart.)
- Everything for a session — the working files, subagent folders, checkpoints — sits under that
  one session folder, so deleting a session is just deleting its folder.

The CLI is the same idea with the numbers turned down: it runs one fixed session in-process and
skips HTTP entirely. See [interfaces.md](interfaces.md).

## Filesystem-first state

There is no session store in memory beyond a thin cache and no database anywhere. The layout of a
session folder — the working zone, subagent zones, the runtime files, the checkpoints — is the
real source of truth. [storage.md](storage.md) draws the full picture. The short version:

- The main agent reads and writes inside `workspace/`.
- Each subagent gets its own folder under `subagents/` and can only *read* the main workspace.
- The chat history is an append-only file (`workspace/.runtime/messages.jsonl`).
- A checkpoint is a full copy of `workspace/`; "undo" restores it.

## How the pieces fit together

```
You
 │  (message)
 ▼
Main agent  ──────────────► think-act loop (LangChain create_agent)
 │   │                        every model call and tool call passes
 │   │                        through the middleware stack:
 │   │                          • Permission (Plan hides write tools)
 │   │                          • Decision (Confirm asks you first)
 │   │                          • Context inject (adds your notes)
 │   │                          • Tool errors (turn failures into
 │   │                            self-correction signals)
 │   │
 │   ├─ file tools ──────────► workspace/ on disk
 │   ├─ json_* tools ────────► JSON documents in workspace/
 │   ├─ knowledge base ──────► RAG search (optional, off by default)
 │   ├─ ask_user / select ───► pauses and waits for your answer
 │   └─ delegate_to_subagent ─► launches a subagent (asyncio task)
 │                                 cold context, own folder,
 │                                 returns {summary, artifacts}
 ▼
Reply (streamed back token by token)
```

## Where to go next

- The loop and middleware in detail: [runtime.md](runtime.md)
- The disk layout and undo: [storage.md](storage.md)
- The tools themselves: [tools.md](tools.md)
- The safety switches and questions: [modes-and-hitl.md](modes-and-hitl.md)
- Using it (CLI, HTTP, web): [interfaces.md](interfaces.md)
- The library mapping: [frameworks.md](frameworks.md)
