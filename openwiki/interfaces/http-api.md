---
type: interface
title: HTTP server, API and SSE stream
description: The FastAPI agent server — startup, the session-scoped route surface with status codes, the asynchronous turn model over one long-lived Server-Sent Events stream, and how events are normalized for the browser.
tags: [http, api, fastapi, sse, interface]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T17:46:46.267Z
sources:
  - id: openwiki-source-005cbaacecc681a00b4b1e60
    resource: repo://core/app/routes/_deps.py
  - id: openwiki-source-53e6f36a073f4241bf57b1e0
    resource: repo://core/app/routes/dev.py
  - id: openwiki-source-4a9e014667c5d2fd5ea34178
    resource: repo://core/app/routes/turn.py
  - id: openwiki-source-bfc9e6c28fb445356f8c6a8e
    resource: repo://core/app/routes/workspace.py
  - id: openwiki-source-3a88e4d15da7c9e7b69e7b29
    resource: repo://core/app/server.py
  - id: openwiki-source-5a1154d06cefdb83d986ffbc
    resource: repo://core/app/sse.py
  - id: openwiki-source-6a40b51ef435a5ba146d037c
    resource: repo://core/models.py
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
---

# HTTP server, API and SSE stream

The HTTP server is the whole backend: one FastAPI process that holds many sessions in memory,
exposes them under `/sessions/{id}/...`, and streams each session's events as Server-Sent Events.
There is no separate orchestrator or database.

## Starting it

- `luna-web` (console script → `core.app.server:run`) starts uvicorn with host, port and reload
  from `ServerSettings` (`HOST`, `PORT`, `RELOAD`).
- Equivalent manual start: `uvicorn core.app.server:create_app --factory --port 8000`.
- Docker: `cd docker && docker compose up --build` (see
  [development and deployment](../operations/development-and-deployment.md)).

`create_app()` sets the ingress `root_path` from `ROOT_PATH`, adds CORS with
`ALLOWED_ORIGINS` (default `*`), and registers four routers: sessions, turns, workspace, and
files & checkpoints. If `devfront/static/` exists next to the code it is mounted at `/` as the
web UI; otherwise a warning is logged and only the API is served. `devfront/` is currently
git-ignored (it is being reworked), so a fresh checkout serves the API only.

The `lifespan` runs on startup: wipe stale session folders under `~/.luna/sessions/`, run
`init_process`, and store a `SessionManager` and the session root on `app.state` (see
[sessions and storage](../concepts/sessions-and-storage.md)).

## Session-scoped dependencies

`core/app/routes/_deps.py` is the single place a `session_id` from the URL is resolved:

- `get_session` looks the id up in the registry and raises `404 Unknown session_id` if absent.
- `session_paths` depends on `get_session`, so the id is always checked against the registry
  **before** it is interpolated into a filesystem path. The ordering is guaranteed by FastAPI's
  dependency graph, not by caller discipline.

## Routes

### Sessions and health

| Route | Result |
|---|---|
| `GET /health` | `{"status": "ok", "pending_hitl": <all sessions>, "sessions_count": n}` |
| `POST /sessions` `{"profile": "standalone"}` | `201 {"session_id", "profile"}`; any other profile fails request validation |
| `GET /sessions` | list with id, profile, `is_busy`, timestamps, both modes |
| `DELETE /sessions/{id}` | `204`; cancels the running turn, waits, removes registry entry and folder; `404` if unknown |

### Turns

| Route | Result |
|---|---|
| `POST /sessions/{id}/turn` `{"text"}` | `202 {"turn_id"}`; the turn runs in the background; `409` if a turn is already running |
| `GET /sessions/{id}/events` | the SSE stream (below) |
| `POST /sessions/{id}/hitl/respond` `{"hitl_id", "value"}` | `{"resolved": id}`; `404` for an unknown id **or** an id belonging to another session (deliberately indistinguishable) |
| `POST /sessions/{id}/stop` | `{"cancelled": bool}` |
| `GET /sessions/{id}/mode` | `{"permission_mode", "decision_mode"}` |
| `POST /sessions/{id}/mode` | set either or both; allowed mid-turn, applies from the next turn |

`TurnRequest` also has an `attachments` field, but it is unused; files are attached through the
attachments routes instead.

### History and attachments

| Route | Result |
|---|---|
| `GET /sessions/{id}/history` | `{"summary", "messages"}` from the session's log, as raw message dicts |
| `POST /sessions/{id}/attachments?path=&overwrite=` (multipart `file`) | `201 {"path", "size", "checkpoint"}` |
| `GET /sessions/{id}/attachments` | `{"files": [{"path", "size", "mtime"}]}` — the same set the agent sees |
| `GET /sessions/{id}/attachments/{path}` | the file as a download |
| `DELETE /sessions/{id}/attachments/{path}` | `{"deleted", "checkpoint"}` |

`path` is relative to `workspace/attachments/` and defaults to the uploaded file's name. Errors
from `WorkspaceManager` map to status codes: path violations `422`, existing file without
`overwrite=true` `409`, over 20 MB `413`, missing file `404`. Upload and delete are refused with
`409 Session is busy` while a turn runs, because a write mid-turn would race the agent's own
writes; listing and download are always allowed. The upload reads at most the cap plus one byte,
so an oversized body is never buffered. Path rules are on
[zones and attachments](../concepts/zones-and-path-scope.md).

### Files and checkpoints (UI side panel)

| Route | Result |
|---|---|
| `GET /sessions/{id}/fs/tree` | recursive tree of `workspace/` and `subagents/` (not `checkpoints/`) with sizes and mtimes |
| `GET /sessions/{id}/fs/file?path=` | `{"path", "content", "size", "mtime"}` for a UTF-8 file up to 1 MiB; `413` if larger, `415` if binary |
| `GET /sessions/{id}/fs/download?path=` | raw bytes, any type and size |
| `GET /sessions/{id}/checkpoints` | `[{"id", "label", "mtime"}]`, newest first |
| `POST /sessions/{id}/checkpoints/{cp_id}/restore` | `{"restored"}`; `409` while busy, `400` for a malformed id, `404` if unknown |

These viewer routes resolve paths against the **session** folder, not through `PathScope`:
`.runtime/` files are readable here (read-only), absolute paths are `400` and escapes are `403`.
Restore semantics are on [checkpoints and undo](../concepts/checkpoints-and-undo.md).

## The turn model

A turn is asynchronous:

1. The client opens `GET /sessions/{id}/events` once and keeps it open.
2. `POST .../turn` returns a turn id immediately.
3. Events for that turn arrive on the open stream; the end of the turn is a `control` event with
   `{"type": "turn_done", "turn_id"}`, **not** the end of the stream. The same stream carries
   every later turn.
4. If the agent asks something, a `custom` event of type `hitl_question` or `hitl_confirm` with a
   `hitl_id` arrives; the client answers with `POST .../hitl/respond` and the parked turn
   continues (see [modes and HITL](../workflows/modes-and-hitl.md)).

Only one turn per session runs at a time, because the agent's history is a single shared list.
Events sit in the session's queue until read, so nothing produced before the client connects is
lost. The queue has one reader at a time: two concurrent `/events` connections to the same
session would split the events between them.

## SSE frames

`core/app/sse.py` drains `session.events()` and writes one frame per event:

```
event: <mode>
data: {"namespace": "main" | "subagent.<type>.<id>", "mode": "...", "data": {...}}
```

If no event arrives for 30 seconds — typically while waiting for a user's answer — a `: ping`
comment is sent to keep proxies from closing the connection. Responses carry
`Cache-Control: no-cache` and `X-Accel-Buffering: no`.

LangChain objects are normalized to plain JSON before serialization:

- **`messages`**: a model chunk becomes `{"text", "reasoning", "tool_call_chunks"?,
  "langgraph_node"?}`; other messages LangGraph emits in this mode are serialized with
  `message_to_dict`.
- **`updates`**: `{node: {"messages": [...]}}` with each message serialized by `message_to_dict`
  — the same shape the history log uses.
- **`custom`**: passed as is, except `subagent_event`, whose nested `messages` / `updates` payload
  is normalized by the same rules so subagent output is readable.
- **`control`**, **`error`**: already plain JSON.

A failure inside normalization is logged and the original data is sent with `default=str`, so
the stream never breaks on an odd payload. Event kinds themselves are described on
[a turn inside BaseAgent](../architecture/agent-turn.md).
