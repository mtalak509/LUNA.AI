# Using LUNA: CLI, HTTP, and the web UI

There are two ways to use LUNA, and they share the same agent core. The **CLI** talks to the agent
directly in one process. The **HTTP server** wraps the agent in a web API and also serves the
**web UI**. This document covers both, plus how the LLM provider is configured.

## Choosing an LLM provider

Both front ends need a language model. LUNA can talk to three kinds of provider, chosen by the
`LLM_PROVIDER` environment variable:

| `LLM_PROVIDER` | What it is |
|---|---|
| `openrouter` | A public gateway to many models under one key (OpenAI-style Chat Completions). |
| `gpustack` | vLLM served through GPUStack, using the OpenAI Responses API. |
| `ollama` | A local Ollama instance via its OpenAI-compatible endpoint. |

All three are OpenAI-compatible, so they're built with the same `ChatOpenAI` client; the only
differences are the endpoint and which API style is used. Addresses and keys go in `.env` (see
`.env.example`). The knowledge-base embedding endpoint is configured separately and doesn't depend
on this flag. Config lives in [core/config.py](../core/config.py).

## The CLI

The CLI is an interactive REPL. It runs one fixed session in-process and drives the agent
directly — no HTTP, no server. It's the quickest way to work with LUNA or to try changes.

```powershell
python -m cli      # or the `luna` console command
```

You get a prompt showing the current modes:

```
[act/accept_all] >
```

Type anything without a leading `/` and it goes to the agent as your message. Type a `/command` to
control the session. When the agent asks you a question, answer at the follow-up prompt.

### Commands

| Command | What it does |
|---|---|
| `/plan` / `/act` | Switch Permission mode (Plan hides write tools; Act allows them). |
| `/confirm` / `/accept` | Switch Decision mode (Confirm asks before writes; Accept-all doesn't). |
| `/ptr [pointer]` | Emulate "the section open in the UI" — sticks to every following turn; no argument clears it. |
| `/attach <path> [--overwrite]` | Copy a file from your machine into `workspace/attachments/`, where the agent will see it. Refuses to clobber an existing file unless you pass the flag. |
| `/fs [path]` | Print the `workspace/` tree (or a subfolder); `.runtime/` is hidden. |
| `/cp` | List checkpoints (id + time). |
| `/undo <id>` | Roll `workspace/` back to a checkpoint and reload history. |
| `/reset` | Recreate the session from scratch (wipe `workspace/`, checkpoints, history). |
| `/help`, `/?` | Show the commands. |
| `/quit` | Exit (Ctrl-C / Ctrl-D also work). |

### Where its files go

The CLI keeps everything under `~/.luna/cli/` — a sibling of the server's `~/.luna/sessions/`, so
the two never clash. Its single session id is fixed (`dev`). Logs go to a file
(`~/.luna/cli/repl.log`), not the console, so the screen stays clean; only the streamed turn is
printed. See [storage.md](storage.md) for the folder layout.

### How the CLI builds the agent

The CLI is a thin shell. On startup it does the process-level setup once (copy the prompts into
place, wire up the clients and the subagent factory), then builds the single session's main agent,
then loops reading your input. Each turn runs through the same `AgentSession` runner the HTTP
server uses, which is what lets the CLI answer the agent's questions concurrently while a turn is
parked. The wiring is in [cli/](../cli/) and the shared build code in
[core/agent/bootstrap.py](../core/agent/bootstrap.py).

## The HTTP server and web UI

The server is a FastAPI app, started with:

```powershell
uvicorn core.app.server:create_app --factory --host 0.0.0.0 --port 8000
```

It holds many sessions at once and serves the built-in web UI (static files under
[devfront/](../devfront/)) at `http://localhost:8000`. There's no separate front-end build step —
the same process serves the API and the page. With Docker: `cd docker && docker compose up
--build`.

The web UI is a chat on one side and a file/checkpoint panel on the other: you talk to the agent,
watch its progress stream in (with subagent work shown as cards), browse the session's files, and
restore checkpoints.

### The API

All the interesting routes are scoped to a session (`/sessions/{id}/...`). Here's the whole
surface:

**Sessions and health**

| Method & path | Purpose |
|---|---|
| `POST /sessions` | Create a session (with a profile). Returns its id. |
| `GET /sessions` | List sessions — busy state, modes, activity timestamps. |
| `DELETE /sessions/{id}` | Cancel any running turn and delete the session (registry + disk). |
| `GET /health` | Process liveness: session count and pending questions. |

**Running a turn**

| Method & path | Purpose |
|---|---|
| `POST /sessions/{id}/turn` | Start a turn in the background (optionally with a `pointer`). Returns a turn id; `409` if the session is already busy. |
| `GET /sessions/{id}/events` | The Server-Sent Events stream — one long-lived stream that carries every turn's events. |
| `POST /sessions/{id}/hitl/respond` | Answer a question the agent is waiting on. |
| `POST /sessions/{id}/stop` | Cancel the active turn (subagent included). |
| `GET/POST /sessions/{id}/mode` | Read or change the Permission / Decision modes. |
| `GET /sessions/{id}/history` | The conversation so far, for rendering the chat. |

**Attachments — the files you give the agent**

| Method & path | Purpose |
|---|---|
| `POST /sessions/{id}/attachments?path=...&overwrite=` | Attach a file (multipart). `201` with the stored path, size, and the checkpoint it left behind. |
| `GET /sessions/{id}/attachments` | Flat listing — path, size, modification time. |
| `GET /sessions/{id}/attachments/{path}` | Download one attachment as-is. |
| `DELETE /sessions/{id}/attachments/{path}` | Delete one attachment, leaving a checkpoint. |

Everything you attach lands in one directory, `workspace/attachments/`, and `path` is relative to
it (optional — it defaults to the uploaded file's own name). Missing folders along the way are
created. That single directory is what the agent watches: on every model call it gets a listing of
it, so a file you delete simply stops being there for the agent, with no stale notification to
correct. See [runtime.md](runtime.md).

Refusals to expect: `422` for a path that tries to leave `attachments/`, is absolute, names a
drive, or starts with a dot; `409` for a file that already exists (retry with `overwrite=true`);
`413` for anything over 20 MB. Uploading and deleting also return `409` while a turn is running,
since a write mid-turn races the agent's own writes — reading and listing are always allowed, so
the UI's file picker keeps working while the agent thinks.

The CLI's `/attach` is the same operation over a different transport: both go through one
`WorkspaceManager` ([core/agent/workspace.py](../core/agent/workspace.py)), which resolves the path
with the same gatekeeper the agent's file tools use and takes the checkpoint. So an undo scenario
you try in the REPL tells you how the server behaves too. That class also carries the general
zone-level operations — write, read, delete, list anywhere inside `workspace/` — and attachments
are the narrow facet of them that the HTTP and CLI surfaces expose today.

**Files and checkpoints** (used by the web UI's side panel)

| Method & path | Purpose |
|---|---|
| `GET /sessions/{id}/fs/tree` | The session's file tree (`workspace/` + `subagents/`). |
| `GET /sessions/{id}/fs/file?path=...` | Read one text file (read-only view). |
| `GET /sessions/{id}/checkpoints` | List checkpoints, newest first. |
| `POST /sessions/{id}/checkpoints/{cp_id}/restore` | Roll `workspace/` back to a checkpoint (rejected while the session is busy). |

### How a turn flows over HTTP

A turn is asynchronous. You `POST .../turn`, which returns immediately with a turn id and starts
the work in the background; the actual output arrives on the `GET .../events` stream you keep open.
That stream is opened once and survives many turns — each turn's end is just a `turn_done` event on
it, not the end of the stream. If the agent parks on a question, you answer with
`.../hitl/respond` and the parked turn continues. One turn runs per session at a time (a second
`POST .../turn` while busy gets a `409`), because the agent's history is a single shared list that
concurrent turns would corrupt.

## Next

- What the modes and questions mean: [modes-and-hitl.md](modes-and-hitl.md)
- What happens inside a turn: [runtime.md](runtime.md)
- Where the session files live: [storage.md](storage.md)
