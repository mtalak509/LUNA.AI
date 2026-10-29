---
type: architecture
title: Architecture overview
description: The big picture of LUNA — a thin harness around one self-driving LangChain agent, a flat shared tool pool, filesystem-first state with no database or checkpointer, in-process subagents, and two front ends over one core.
tags: [architecture, overview, agent, filesystem, langchain]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T18:35:21.080Z
sources:
  - id: openwiki-source-c46570de609dc2e9a293d8a2
    resource: repo://cli/config.py
  - id: openwiki-source-7d64ce77652175af9c727d3b
    resource: repo://cli/session.py
  - id: openwiki-source-364a89a77ff1b7418dc1c6c3
    resource: repo://core/agent/base.py
  - id: openwiki-source-9cae2963ffbc49ec962d3395
    resource: repo://core/agent/bootstrap.py
  - id: openwiki-source-8663d578d4a41ad0e6edc1c1
    resource: repo://core/agent/pool.py
  - id: openwiki-source-277976f7cb0a0aa61d455571
    resource: repo://core/agent/tools/delegation_tools.py
  - id: openwiki-source-3a88e4d15da7c9e7b69e7b29
    resource: repo://core/app/server.py
  - id: openwiki-source-27ee44b17a928f4d359d8b25
    resource: repo://core/config.py
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
---

# Architecture overview

LUNA is a personal work assistant for documents and files. You write a request in plain
language; a language model works out the steps and carries them out by calling **tools** —
find a file, search inside it, read a window of lines, apply an edit.

Architecturally LUNA is a **thin harness around one agent**, not a workflow system. There is no
router, intent classifier or orchestration graph deciding what happens next. The model picks
every next step itself, and the route through a task is simply the sequence of tool calls it
chose. LUNA's job is to give that model good tools, a safe place to work, and a way to stream
progress back.

## The ideas the code rests on

**The agent drives itself.** Each agent is one LangChain `create_agent` instance wrapped by
`BaseAgent` (`core/agent/base.py`). There is no `StateGraph` with classify/route/plan nodes in
front of it, and delegation to a helper is an ordinary tool call, not a graph edge.

**Tools are shared; specialization is a prompt.** There is one flat list of tools,
`FULL_POOL` (`core/agent/pool.py`). Every agent gets a slice of it computed by *subtraction*
from declarative attributes on each tool (see [the tool pool](../tools/tool-pool.md)). A
"specialist" subagent is the same `BaseAgent` class started with a different instruction file.

**State lives in files.** Conversation history, working documents, notes and undo snapshots are
all files under a session folder. There is no database, and the LangGraph graph runs **without a
checkpointer** — LUNA keeps the message list itself and mirrors it to an append-only log. A
checkpointer would be a second source of truth that could drift from the files. Undo is a folder
copy, and a pending question to the user is a suspended coroutine rather than a LangGraph
interrupt.

## The pieces

```
front end (HTTP server or CLI)
   │
   ▼
AgentSession  ── runs one turn at a time as an asyncio.Task, events → queue
   │
   ▼
BaseAgent (main) ── create_agent think-act loop
   │   middleware on every call: tool errors · Plan/Act · Confirm · working context
   │
   ├─ file tools ─────────► workspace/ (PathScope-checked paths, checkpoints after writes)
   ├─ knowledge base ─────► RAG over Qdrant (optional, off by default)
   ├─ ask_user / select ──► parks the turn until the user answers
   └─ delegate_to_subagent ► BaseAgent (subagent) in the same event loop
                               cold context, own folder, returns {summary, artifacts}
```

### The main agent

Each session has exactly one main agent. It is the only thing the user talks to, it owns the
conversation, and it has the full tool slice. `run_stream` runs the think-act loop for one turn
and yields namespaced stream events (see [a turn inside BaseAgent](agent-turn.md)).

### Subagents

The main agent can hand a self-contained sub-task to a subagent through the
`delegate_to_subagent` tool. The subagent is another `BaseAgent` with a cold context (only the
task text), its own prompt (`agents/<type>/agent.md`) and its own folder. It runs as part of the
same asyncio task, one level deep, and **advises rather than edits**: it can read the main
workspace but writes only to its own zone, and returns `{summary, artifacts}`. The main agent
stays the single writer of the user's documents. See
[delegation](../workflows/delegation-and-subagents.md).

### Isolation by paths, not processes

Agents share one process. Their boundary is enforced by `PathScope`
(`core/agent/tools/fs_paths.py`), which every file tool resolves its path through: the main
agent writes in `workspace/`, a subagent only in `subagents/<uid>/`, and nobody touches
`.runtime/`. See [zones and PathScope](../concepts/zones-and-path-scope.md).

### Safety switches

Two independent per-session modes control freedom: **Plan/Act** (may the agent write at all)
and **Confirm/Accept-all** (must each write be approved). Both are enforced in middleware at the
execution boundary, not left to the model. See [middleware](middleware.md) and
[modes and HITL](../workflows/modes-and-hitl.md).

## One process, many sessions — or one

Process setup and per-session setup are split in `core/agent/bootstrap.py`:

- `init_process` runs once: it copies the prompts into a process `procedures/` folder and
  installs process-wide holders (service clients, the subagent factory, the HITL registry).
- `build_session_agent` builds one main agent for one session folder.

The **HTTP server** (`core/app/server.py`) calls `init_process` in its lifespan and keeps a
`SessionManager` registry of many live sessions under `~/.luna/sessions/`. The **CLI** (`cli/`)
runs the same pair for one fixed session under `~/.luna/cli/dev/` and drives it in-process
without HTTP. Both drive turns through the same `AgentSession` runner, so behaviour is the same
in both. See [sessions and storage](../concepts/sessions-and-storage.md),
[HTTP API](../interfaces/http-api.md) and [CLI](../interfaces/cli.md).

The session registry is in memory only. On server start, leftover session folders from the
previous process are wiped, so sessions never survive a restart.

## LangChain and LangGraph usage

LUNA uses LangChain v1 / LangGraph 1 as building blocks:

- `create_agent` for the loop, tool calling and middleware hooks (`awrap_model_call`,
  `awrap_tool_call`);
- plain `@tool` functions wrapped by `agent_tool` to carry filtering metadata;
- one `ChatOpenAI` client for all three providers (OpenRouter, GPUStack/vLLM, Ollama);
- `astream` with `messages` / `updates` / `custom` modes plus `get_stream_writer()` for tool
  events.

Deliberately not used: an orchestration graph, a checkpointer, `interrupt`/resume for questions
(it would need a checkpointer and re-runs the interrupted node, which is unsafe for tools with
side effects), the built-in summarization middleware (LUNA compacts once at the turn boundary —
see [history and compaction](history-and-compaction.md)), and provider-specific model packages.

## Where things live

| Area | Code |
|---|---|
| Agent loop | `core/agent/base.py` |
| Middleware | `core/agent/middleware/` |
| History, compaction, checkpoints | `core/agent/messages.py`, `compaction.py`, `checkpoint.py` |
| Tools and the pool | `core/agent/tools/`, `core/agent/pool.py` |
| Sessions and bootstrap | `core/agent/session.py`, `core/agent/bootstrap.py` |
| User-side file operations | `core/agent/workspace.py` |
| HTTP server | `core/app/` |
| CLI | `cli/` |
| Prompts | `core/procedures/` |
| Config | `core/config.py` |
| RAG services, HTTP clients | `core/services/`, `core/clients/` |

Why the project is shaped this way, and how new work is decided, is covered in
[planning and direction](../project/planning-and-direction.md).
