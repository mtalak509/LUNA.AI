---
type: concept
title: Sessions, process bootstrap and on-disk layout
description: Where LUNA keeps state on disk, how a process and a session are set up, how SessionManager and the AgentSession runner manage turns, and what survives a restart.
tags: [sessions, storage, bootstrap, filesystem, lifecycle]
sources:
  - id: openwiki-source-a87578127072d9fd00cf3737
    resource: repo://cli/app.py
  - id: openwiki-source-0999e357957975c8879ee999
    resource: repo://cli/commands.py
  - id: openwiki-source-c46570de609dc2e9a293d8a2
    resource: repo://cli/config.py
  - id: openwiki-source-364a89a77ff1b7418dc1c6c3
    resource: repo://core/agent/base.py
  - id: openwiki-source-9cae2963ffbc49ec962d3395
    resource: repo://core/agent/bootstrap.py
  - id: openwiki-source-07e76361ddee296c51381ff5
    resource: repo://core/agent/session.py
  - id: openwiki-source-3a88e4d15da7c9e7b69e7b29
    resource: repo://core/app/server.py
  - id: openwiki-source-27ee44b17a928f4d359d8b25
    resource: repo://core/config.py
generated: { by: "codex", at: "2026-11-09T20:38:09.193Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-11-09T20:45:41.933Z
---

# Sessions, process bootstrap and on-disk layout

LUNA has no database. A session combines a folder, a live agent and an in-memory shell-task
manager. Documents and history are files; live process ownership is not persisted. This page covers the folder layout, how the process
and each session are built, and the runner that executes turns.

## The home folder

All on-disk state lives under `LUNA_HOME = Path.home() / ".luna"` (`core/config.py`). The path
is fixed in code and deliberately not configurable through the environment; `Path.home()`
resolves it per OS, so Windows gets `%USERPROFILE%\.luna` with no special code.

The two front ends use separate subfolders so they never clobber each other:

```
~/.luna/
├── sessions/                  HTTP server: the process root
│   ├── procedures/            prompts copied here at startup
│   └── <uuid4 hex>/           one folder per session
└── cli/                       CLI: the process root
    ├── procedures/
    ├── repl.log               CLI log file
    ├── history                CLI input history
    └── dev/                   the CLI's single fixed session
```

## A session folder

```
<session>/
├── workspace/                 the main agent's read-write zone
│   ├── notes/decisions.md     running notes, injected into context each model call
│   ├── attachments/           files the user attached
│   ├── artifacts/             results worth keeping
│   ├── .truncated_outputs/    full text of cut tool output (read-only for the agent)
│   └── .runtime/
│       ├── messages.jsonl     the conversation log
│       ├── blobs/             externalized large strings
│       └── hitl_pending.json  present only while the main agent waits for an answer
├── checkpoints/               full copies of workspace/ (undo)
├── shell_tasks/<sh_id>.log     bounded combined stdout/stderr logs, outside checkpoints
└── subagents/<uid>/           one zone per delegated subagent
    ├── notes/  artifacts/  .runtime/
```

`build_session_agent` creates `workspace/notes/`; the other folders appear when something first
writes there. Deleting a session is deleting this folder. Who may read or write which part is
covered in [zones and PathScope](zones-and-path-scope.md); the history file in
[history and compaction](../architecture/history-and-compaction.md); checkpoints in
[checkpoints and undo](checkpoints-and-undo.md).

## Process setup vs session setup

`core/agent/bootstrap.py` splits setup in two.

**`init_process(root)` — once per process.**

1. Copies the repository's `core/procedures/` into `<root>/procedures/` (overwriting on every
   start, so prompt edits take effect after a restart). This copy is the `procedures_root` every
   agent of the process loads its prompt from.
2. Installs the process-wide holders that tools reach without going through the graph:
   `set_clients` (service clients such as RAG, built from config), `set_subagent_factory` (the
   factory that builds subagents from the full pool and `procedures_root`) and
   `set_hitl_registry` (the registry of pending questions).

These holders carry no per-session state. Anything session-specific — workspace path, session
id, modes — travels in the per-turn runtime context instead.

**`build_session_agent(session_root, procedures_root)` — once per session.** It builds the main
agent: prompt `main_agent.ru.md` loaded from `procedures_root`, the tool slice for scope `main`
minus disabled features, namespace `main`, workspace `<session_root>/workspace`, checkpoints
`<session_root>/checkpoints`, and one new `ShellManager(session_root)`. Delegation passes that
same manager into each subagent; it is not a process-wide holder or a per-turn object.

`build_agent_factory` wraps that into a `{profile: factory}` dict. The only profile is
`standalone`. `build_agent` is a deprecated shim that does both steps at once.

## SessionManager — the registry

`SessionManager` (`core/agent/session.py`) is the HTTP server's in-memory registry. It has no HTTP
code of its own; the routes sit on top of it.

- `create_session(profile)` looks up the profile's factory (unknown → `ValueError`), mints a
  `uuid4().hex` id, creates the folder with `exist_ok=False`, builds the agent and wraps it in
  an `AgentSession` with server defaults **`act` / `confirm`**.
- `list_sessions()` reports id, profile, busy flag, creation time, last activity and both modes.
- `delete_session(id)` awaits `session.close()` before deleting the folder and dropping the
  record. Failed cleanup preserves the record and files so deletion can be retried.
- `close()` rejects new sessions and attempts to close every registered session without
  deleting their files. A failed close can be retried.

Because session ids are minted by the backend as hex strings, they are path-safe by
construction; the HTTP layer still resolves an id against the registry before it ever builds a
path from it.

## AgentSession — the turn runner

`AgentSession` decouples running a turn from consuming its events. Both front ends use it.

- `run_turn(text)` starts `BaseAgent.run_stream` as an `asyncio.Task` and returns a turn id
  (`turn-1`, `turn-2`, …) immediately. If a turn is already running it raises `RuntimeError`:
  the agent's message list is shared and mutable, so concurrent turns would corrupt it.
- The task pushes every `StreamEvent` into an `asyncio.Queue` and always finishes with a
  `control` event `turn_done`. A cancellation adds `turn_cancelled` first; an unexpected error
  adds an `error` event.
- `events()` is an endless async generator over that queue. It does not stop at `turn_done`, so
  one consumer (one SSE connection) can follow many turns.
- `cancel_turn()` cancels the task; the cancellation unwinds through the main agent and any
  subagent running inside it. Awaited foreground shell tasks stop, while background ones
  remain running.
- `close()` rejects new turns, cancels and awaits the active turn, then closes the shell
  manager. Cleanup is shielded from caller cancellation, repeatable and retryable on failure.
- `resolve_hitl(hitl_id, value)` answers a parked question through the process HITL registry,
  scoped to this session's id (see [modes and HITL](../workflows/modes-and-hitl.md)).
- `permission_mode` and `decision_mode` are plain attributes read when a turn starts, so
  changing them mid-turn affects the next turn.

## Lifecycle and restarts

- **Server start.** The lifespan first wipes every directory under `~/.luna/sessions/` —
  including `procedures/`, which `init_process` immediately recreates — and only then
  initializes. The registry is in memory, so old folders would be orphans nobody can reach.
- **Work.** Each message runs a turn; successful file-tool writes leave checkpoints. Shell
  calls do not. Background tasks survive answers and mode changes, including switching to Plan.
- **End.** Sessions end only through `DELETE /sessions/{id}` or a process restart. There is no
  idle timeout and no session-count limit yet. Server lifespan shutdown closes all sessions
  and their shell tasks in `finally` before the process exits.
- **CLI.** The CLI does not wipe on start. Its fixed session `~/.luna/cli/dev/` is reopened and
  the agent loads the existing `messages.jsonl`, so the conversation and workspace carry over
  between CLI runs. `/reset` first closes the old session, then wipes its folder and constructs
  a replacement; failed close leaves the old session and files intact. Every REPL exit closes
  the current session, including exceptional exits. The log in the parent folder is untouched.

The task registry is never rehydrated from `shell_tasks/` logs: a new CLI process gets an
empty manager even when those logs remain. The manager stops tasks with reason `session_closed`
on close. See [host shell](../tools/host-shell.md) for task limits and process ownership.

See [HTTP API](../interfaces/http-api.md) and [CLI](../interfaces/cli.md) for how each front
end drives sessions.
