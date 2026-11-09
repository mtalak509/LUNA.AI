---
type: quickstart
title: LUNA quickstart and task map
description: What LUNA is, how to run it, and a routing map from common development tasks to the wiki pages and source files that own them.
tags: [quickstart, overview, navigation, onboarding]
sources:
  - id: openwiki-source-ea70eb6c045047448e446296
    resource: repo://.gitignore
  - id: openwiki-source-a87578127072d9fd00cf3737
    resource: repo://cli/app.py
  - id: openwiki-source-0999e357957975c8879ee999
    resource: repo://cli/commands.py
  - id: openwiki-source-364a89a77ff1b7418dc1c6c3
    resource: repo://core/agent/base.py
  - id: openwiki-source-9cae2963ffbc49ec962d3395
    resource: repo://core/agent/bootstrap.py
  - id: openwiki-source-ef44557fa5de39f25a459d70
    resource: repo://core/agent/shell/models.py
  - id: openwiki-source-4c191222105a52799537e04a
    resource: repo://core/agent/shell/shell_manager.py
  - id: openwiki-source-277976f7cb0a0aa61d455571
    resource: repo://core/agent/tools/delegation_tools.py
  - id: openwiki-source-e1f3d5a721b9517f9aa6ba09
    resource: repo://core/agent/tools/files_tools.py
  - id: openwiki-source-6e479670aac673f34064e1c5
    resource: repo://core/agent/tools/shell_tools.py
  - id: openwiki-source-23777f9174e92ec97c2941a7
    resource: repo://core/app/routes/shell.py
  - id: openwiki-source-3a88e4d15da7c9e7b69e7b29
    resource: repo://core/app/server.py
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
generated: { by: "codex", at: "2026-11-09T20:38:09.193Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-11-09T20:38:09.193Z
---

# LUNA quickstart and task map

**LUNA** is a personal work assistant for reading and editing documents and files. You ask in
plain language; one LLM agent does the work by calling general-purpose file tools and host
shell commands. It is a thin
harness around a LangChain v1 `create_agent` loop: no workflow engine, no database, no LangGraph
checkpointer. Documents and history live under `~/.luna/`; live shell task ownership is in
memory. There are two front ends over one core:
a CLI REPL and a FastAPI server (which can also serve a web UI).

Start with the [architecture overview](architecture/overview.md).

## Run it

```powershell
py -3.11 -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -e .[dev]
copy .env.example .env      # set LLM_PROVIDER and that provider's URL / key / model
luna                        # CLI REPL  (python -m cli)
luna-web                    # HTTP server on :8000 (uvicorn core.app.server:create_app --factory)
pytest                      # offline tests
```

Python 3.11 only. `LLM_PROVIDER` is `openrouter` (code default), `gpustack` or `ollama`. See
[development and deployment](operations/development-and-deployment.md) and
[LLM providers and configuration](integrations/llm-providers-and-config.md).

## Task map

| I want to… | Read | Main code |
|---|---|---|
| understand the whole system | [architecture overview](architecture/overview.md) | `core/agent/base.py`, `core/agent/bootstrap.py` |
| follow one turn / debug a turn that fails | [a turn inside BaseAgent](architecture/agent-turn.md) | `BaseAgent.run_stream`, `AgentSession` |
| change Plan/Act, Confirm, or the injected context | [middleware](architecture/middleware.md) | `core/agent/middleware/` |
| change how history is stored or compacted | [history and compaction](architecture/history-and-compaction.md) | `core/agent/messages.py`, `compaction.py` |
| add or change a tool | [tool pool](tools/tool-pool.md), [file tools](tools/file-tools.md) | `core/agent/tools/`, `core/agent/pool.py` |
| run commands, manage background work or debug cleanup | [host shell](tools/host-shell.md) | `core/agent/shell/`, `core/agent/tools/shell_tools.py` |
| change what an agent may read or write | [zones and PathScope](concepts/zones-and-path-scope.md) | `core/agent/tools/fs_paths.py` |
| change attachments / user uploads | [zones and PathScope](concepts/zones-and-path-scope.md) | `core/agent/workspace.py` |
| change undo | [checkpoints and undo](concepts/checkpoints-and-undo.md) | `core/agent/checkpoint.py` |
| change session layout or startup | [sessions and storage](concepts/sessions-and-storage.md) | `core/agent/session.py`, `bootstrap.py`, `core/config.py` |
| add or change a subagent | [delegation](workflows/delegation-and-subagents.md), [prompts](project/prompts-and-procedures.md) | `delegation_tools.py`, `core/procedures/agents/` |
| change agent questions / confirmations | [modes and HITL](workflows/modes-and-hitl.md) | `core/agent/tools/_hitl.py`, `hitl_tools.py` |
| edit the agent's instructions | [prompts and procedures](project/prompts-and-procedures.md) | `core/procedures/main_agent.ru.md` |
| touch the HTTP API or SSE | [HTTP API](interfaces/http-api.md) | `core/app/` |
| touch the CLI | [CLI](interfaces/cli.md) | `cli/` |
| switch or debug the LLM provider | [LLM providers](integrations/llm-providers-and-config.md) | `build_chat_model`, `core/agent/patches/` |
| enable the knowledge base | [RAG](integrations/rag-knowledge-base.md) | `core/services/`, `rag_tools.py` |
| write tests or a live scenario | [tests and evals](testing/tests-and-live-evals.md) | `tests/`, `tests/evals/` |
| propose a new feature | [planning and design rules](project/planning-and-direction.md) | `planning/` |
| update this documentation | [development and deployment](operations/development-and-deployment.md#documentation-workflow) | `openwiki/INSTRUCTIONS.md`, `.openwikiignore` |
| build the Docker image / cut a release | [development and deployment](operations/development-and-deployment.md) | `docker/`, `pyproject.toml` |

## Things that surprise people

- **The graph has no memory.** LUNA keeps the message list itself and mirrors it to
  `workspace/.runtime/messages.jsonl`; undo restores that file with the documents.
- **Subagents are not processes.** They run inside the main agent's tool call; isolation is by
  path rules for file tools. Shell bypasses those rules and runs with host-user filesystem
  and network access for both main and subagents.
- **Shell tasks belong to the session.** Background work survives answers, subagent completion
  and Plan transitions. Session close stops it; the task registry is not recovered on restart.
- **Shell has no checkpoint or undo.** Foreground defaults to 120 seconds; background has no
  default deadline. Inspect exit codes and page output with byte `next_offset`.
- **Plan blocks every shell action; Confirm gates every call.** Users can still list/stop
  tasks directly with `/shell tasks`, `/shell stop <id>` and session HTTP task controls.
- **Plan mode is enforced twice** — write tools are hidden and also blocked if called.
- **Sessions do not survive a server restart**; leftover session folders are wiped at startup.
  The CLI's single session does carry over between runs until `/reset`.
- **The runtime prompt is the Russian one** (`main_agent.ru.md`), so the agent answers in
  Russian, and tool results and error texts from the core are Russian too. The CLI's own menus,
  notes and help are English. Shell metadata and diagnostics are also English, while command
  output comes directly from the executed program.
- **`devfront/` (web UI) is git-ignored** while it is reworked: the server serves only the API
  on a clean checkout, and the Docker build expects the folder to exist.

## Conventions in one breath

Python 3.11, line length 100, ruff + black + mypy (`disallow_untyped_defs`). English code
comments only where they clarify. No database, no checkpointer, no orchestration graph. Every
behaviour change ships with unit tests and a live DeepEval scenario. Conventional Commits in
English; releases are manual.
