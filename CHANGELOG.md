# Changelog

All notable changes to LUNA are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.1] — 2026-07-31

### Added
- **Attachments** — upload, list, download, and delete files under `workspace/attachments/`
  via HTTP (`/sessions/{id}/attachments`) and the CLI (`/attach`). Multipart upload needs
  `python-multipart`.
- **Attachments tab (web UI)** — the old Document tab is gone; the side panel manages
  attachments with multi-file upload (button and drag-and-drop), download, delete.
- **Download from the Files tree** — each file row has a ⤓ control; new
  `GET /sessions/{id}/fs/download?path=...` streams the file as-is (binary included, no
  UTF-8 / 1 MiB cap of `fs/file`).

### Changed
- **Patch chip in the feed** — labeled `json_patch` (the real tool name) and shows the file
  path from the event.

### Removed
- **`pointer` remnant** — dropped the unused “section pointer” path inherited from the parent
  project: `pointer` on `TurnRequest`, `run_turn` / `run_stream`, `AgentRuntimeContext`, and
  the REPL `/ptr` command. The value reached turn context but nothing read it (middleware,
  tools, or the web UI).

### Updated
- **documentation** — updated the interfaces.md file to reflect the new attachments API.

## [0.1.0] — 2026-07-23

First public release of LUNA — a personal work assistant for reading and editing documents and
files.

### Added
- **Agent core** — a single main agent running a think-act (ReAct) loop on LangChain
  `create_agent` / LangGraph. History is kept on disk (no LangGraph checkpointer), and long
  conversations are compacted once and reused so they stay inside the context window.
- **Subagents** — the main agent can hand a self-contained sub-task to a specialist via
  `delegate_to_subagent`. Subagents run in-process (asyncio) with a cold context and their own
  folder, and return `{summary, artifacts}` as advisors — they never edit the main documents.
- **Tools** — one flat, shared tool pool: file tools (read / write / edit / list / search /
  delete), structural JSON tools (`json_inspect` / `json_search` / `json_patch`) for working with
  large documents without loading them wholesale, an optional two-step knowledge-base search
  (Qdrant, off by default), and human-in-the-loop tools (`ask_user` / `select_from_options`).
  Documents are arbitrary JSON addressed by path — there is no privileged document and no domain
  schema.
- **Safety modes** — Permission (Plan / Act) hides and blocks write tools in read-only mode;
  Decision (Confirm / Accept-all) gates every write on your approval.
- **Checkpoints and undo** — every successful write snapshots the workspace; restoring a
  checkpoint rewinds both the documents and the conversation.
- **Interfaces** — a self-contained CLI REPL (`python -m cli` / `luna`) and a FastAPI server
  (`core.app.server:create_app`) that serves a built-in web UI and streams turns over
  Server-Sent Events. The server holds many sessions per process, each isolated on disk under
  `~/.luna/`.
- **LLM providers** — `LLM_PROVIDER` selects `gpustack` (vLLM via GPUStack, Responses API),
  `openrouter`, or `ollama`, all built on one OpenAI-compatible client. The package version is
  read from installed metadata (`cli/banner.py`), with `pyproject.toml` as the single source of
  truth.

[0.1.1]: https://github.com/mtalak509/LUNA.AI/releases/tag/v0.1.1
[0.1.0]: https://github.com/mtalak509/LUNA.AI/releases/tag/v0.1.0
