# LUNA documentation

LUNA is a personal work assistant for reading and editing documents and files. You talk to it
in plain language ("find where the price is set in this report and change it to 42"), and it
does the work by calling tools — reading files, searching inside large JSON, applying edits.

Under the hood LUNA is a **thin harness around one LLM agent**. There is no workflow engine and
no database. The agent decides what to do next by itself, one tool call at a time, and all state
lives as plain files on disk. You use it through two front doors: a **command-line REPL** and a
**web UI** (both talk to the same agent core).

This folder explains how LUNA is built and how it behaves. The goal is that you can read it and
understand the whole system without digging through the code.

## Where to start

Read these in order the first time. Each is short and focused.

1. **[architecture.md](architecture.md)** — the big picture. What the agent is, how the main
   agent and its subagents relate, and the three ideas the whole design rests on. Start here.
2. **[runtime.md](runtime.md)** — what actually happens when you send a message: the think-act
   loop, the middleware that wraps every step, how events stream back, and how long chats stay
   inside the context window.
3. **[storage.md](storage.md)** — where everything lives on disk: sessions, the working folder,
   subagent folders, and checkpoints (the undo history).
4. **[tools.md](tools.md)** — the tools the agent can call (files, JSON, knowledge base,
   delegation, asking you a question) and how each agent only gets the tools it's allowed.
5. **[modes-and-hitl.md](modes-and-hitl.md)** — the two safety switches (Plan/Act and
   Confirm/Accept-all) and how the agent pauses to ask you something.
6. **[interfaces.md](interfaces.md)** — the two ways to use LUNA: the CLI and the HTTP server +
   web UI, including the full list of HTTP endpoints.
7. **[frameworks.md](frameworks.md)** — how LUNA maps onto LangChain and LangGraph, and which
   parts of those libraries it deliberately does *not* use.

Plus **[tech-debt.md](tech-debt.md)** — a living list of known rough edges and deferred work.
It is a working registry, not a description of the design.

## Running LUNA

You need **Python 3.11** and a reachable LLM provider (set up in `.env`). From the repo root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
copy .env.example .env    # then fill in your LLM endpoint

python -m cli             # the command-line assistant (talks to the agent directly)
# or
uvicorn core.app.server:create_app --factory --host 0.0.0.0 --port 8000   # HTTP server + web UI
```

The CLI needs a working LLM provider and nothing else. The HTTP server additionally serves the
web UI at `http://localhost:8000`. Both keep their files under `~/.luna/` (see
[storage.md](storage.md)).

## A note on language

These documents are written in plain English on purpose — the aim is a clear mental model, not
exhaustive jargon. Comments and identifiers in the code are English too. The only file here that
is still in Russian is `tech-debt.md`, kept verbatim as a working registry.
