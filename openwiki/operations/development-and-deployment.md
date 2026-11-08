---
type: operations
title: Development setup, tooling and deployment
description: How to install and run LUNA for development, the lint/format/type-check and test settings, the Docker image and its current caveats, helper scripts, and the release and commit conventions.
tags: [operations, development, docker, tooling, release]
sources:
  - id: openwiki-source-ea70eb6c045047448e446296
    resource: repo://.gitignore
  - id: openwiki-source-e119253b3c3737247dc63f2a
    resource: repo://.openwikiignore
  - id: openwiki-source-a2371d6362e5db4bc834ad03
    resource: repo://CLAUDE.md
  - id: openwiki-source-89d8131964331b4892ee1631
    resource: repo://cli/banner.py
  - id: openwiki-source-5be27729131e30130689f927
    resource: repo://docker/docker-compose.yml
  - id: openwiki-source-7f1ca00753e95588bc8efa39
    resource: repo://docker/Dockerfile
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
generated: { by: "claude-code", at: "2026-10-29T18:35:21.080Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T18:35:21.080Z
---

# Development setup, tooling and deployment

## Environment

LUNA requires **Python 3.11 exactly** (`requires-python = ">=3.11,<3.12"`). The development
environment is Windows-first (PowerShell), so watch for file-handle locks (an open log file blocks
`shutil.rmtree`) and CRLF/LF differences; tests that compare files normalize line endings for
that reason.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]          # + pytest, pytest-asyncio, respx, ruff, black, mypy
pip install -e .[dev,eval]     # + DeepEval for live scenarios
copy .env.example .env         # fill in the LLM endpoint
```

The package is `luna-agent`; setuptools packages `core*` and `cli*`. Two console scripts are
installed:

| Command | Entry point | What it starts |
|---|---|---|
| `luna` | `cli.app:run` | the CLI REPL (also `python -m cli`) — see [CLI](../interfaces/cli.md) |
| `luna-web` | `core.app.server:run` | uvicorn with the HTTP server — see [HTTP API](../interfaces/http-api.md) |

Configuration is read from the environment and `.env` (see
[LLM providers and configuration](../integrations/llm-providers-and-config.md)). Runtime state
goes to `~/.luna/`, never into the repository.

The runtime version shown in the CLI banner comes from installed package metadata
(`importlib.metadata.version("luna-agent")`), so `pyproject.toml` is the single source of truth;
running from source without an install shows `0.0.0+dev`.

## Code quality

| Tool | Settings |
|---|---|
| ruff | target py311, line length 100, rules `E F I UP B ASYNC` |
| black | target py311, line length 100 |
| mypy | Python 3.11, `disallow_untyped_defs`, `ignore_missing_imports`, warn on unused ignores |

```powershell
ruff check . ; black . ; mypy core
```

Conventions from `CLAUDE.md`: code comments in English and only where they clarify (older
Russian comments are translated when touched); no commented-out or dead code; extract cohesive
helpers into concept-named modules (like `fs_paths`), never a `utils` dump; dependencies point
downward with no cycles; no database layer and no LangGraph checkpointer.

## Tests

`pytest` runs the offline suite. Defaults in `pyproject.toml`: `asyncio_mode = "auto"`,
`testpaths = ["tests"]`, the repo root on `sys.path`, `--import-mode=importlib` (so same-named
test files in different folders do not clash), and `-m 'not integration'`, which excludes live
tests. Live tests run with `pytest -m integration`; the DeepEval scenarios with
`deepeval test run tests/evals -m integration`. Details on
[tests and live evals](../testing/tests-and-live-evals.md).

## Docker

`docker/Dockerfile` builds a two-stage image for the HTTP server:

- **builder**: `python:3.11-slim`, a venv in `/opt/venv`, `pip install .` from `pyproject.toml`
  and `core/` only (the CLI package is not copied, so the image is server-only);
- **runtime**: `python:3.11-slim` with `bash`, `jq`, `git` and `libgomp1`, the venv, `core/`,
  `scripts/`, a copy of `core/procedures/` at `/procedures/` (the runtime itself still reads the
  prompts from the package), and `devfront/static/`; runs as `appuser` (uid 1000) on port 8000.

Configuration is not baked into the image. `docker/docker-compose.yml` builds the image from the
repo root, maps port 8000 and passes `../.env` as `env_file`. Sessions live inside the container
under `~appuser/.luna/sessions` and die with it.

Caveats in the current tree:

- The Dockerfile copies `devfront/static/`, but `devfront/` is listed in `.gitignore` while the
  web UI is being reworked, so a build from a clean checkout fails at that step unless the
  folder exists locally.
- `libgomp1` is installed for `fastembed`, which is still a declared dependency but is no longer
  used by the code (see [RAG](../integrations/rag-knowledge-base.md)).

## Helper scripts

| Script | Purpose |
|---|---|
| `scripts/dump_tool_schemas.py` | print the tools in OpenAI function-calling format, exactly as the model sees them; `--scope`, `--mode`, `-n`, `--names-only` |
| `scripts/inspect_chat_model.py` | send one prompt through `build_chat_model` and show stream chunks and the aggregate (reasoning patch included) |
| `scripts/list_gpustack_models.py` | list models on the configured GPUStack |

## What is not in the repository

`.gitignore` keeps local-only material out: `.env`, `.mcp.json`, `devfront/`, `specs/` (feature
specs and plans, one folder per feature), `notes/`, `tech-debt/` and `docs/tech-debt.md` (the
tech-debt registry), session working folders, eval run artifacts (`tests/evals/.runs/`) and the
transient OpenWiki run state (`openwiki/.run.json`).

## Documentation workflow

The project documentation is this wiki in `openwiki/`; there is no hand-written `docs/` folder.
The wiki is generated by [OpenWiki](https://github.com/langchain-ai/openwiki) and every material
fact on a page is a *Claim* tied to source lines, so an update can tell which pages drifted.

- **Reading.** Coding agents query it on demand through the OpenWiki MCP tools
  (`openwiki_search`, `openwiki_read`); people read the Markdown directly or run
  `openwiki visualize`. The code stays authoritative.
- **Updating.** At the end of a feature branch, before the pull request, run an OpenWiki update
  (in Claude Code: "Update OpenWiki", through the `openwiki` skill in `.claude/skills/`). It
  compares the repository with the last documented state, rewrites only affected pages and
  re-checks stale claims; a clean tree is a no-op. The wiki changes then ship in the same PR as
  the code.
- **Never hand-edit generated pages**, claims, indexes or manifests. To change *what* or *how* the
  wiki documents, edit the code or `openwiki/INSTRUCTIONS.md` — the hand-written scope and voice
  file that OpenWiki reads but never rewrites — and run an update.
- **Exclusions.** `.openwikiignore` keeps local and private material (`notes/`, `specs/`,
  tech-debt notes, `.env`, `.mcp.json`) out of the wiki entirely.
- **What to commit.** Everything under `openwiki/` (pages, `index.md` files, `.claims/`,
  `.page-manifest.json`, `.last-update.json`) plus the managed blocks in `AGENTS.md` and
  `CLAUDE.md`; `openwiki/.run.json` is ignored.

## Commits and releases

Commits follow Conventional Commits in English: `type(scope): imperative summary` with types
`feat fix refactor chore docs test perf build ci`. The project rules forbid co-author trailers
and any AI or tool attribution in commits and PR descriptions.

Releases are cut **manually by the maintainer**, never automatically. The procedure:

1. Pre-flight: `pytest`, `ruff check .`, `black --check .`, `mypy core` all green.
2. Bump `version` in `pyproject.toml` (pre-1.0: breaking → minor, otherwise patch).
3. Move `CHANGELOG.md` `[Unreleased]` to `## [X.Y.Z] — YYYY-MM-DD` and open a fresh
   `[Unreleased]`.
4. Update the version line and history in `CLAUDE.md`, and `README.md` if needed.
5. Commit `chore(release): vX.Y.Z` and create an annotated tag `vX.Y.Z`.

The changelog follows Keep a Changelog and Semantic Versioning.
