```
██╗     ██╗   ██╗███╗   ██╗ █████╗
██║     ██║   ██║████╗  ██║██╔══██╗
██║     ██║   ██║██╔██╗ ██║███████║
██║     ██║   ██║██║╚██╗██║██╔══██║
███████╗╚██████╔╝██║ ╚████║██║  ██║
╚══════╝ ╚═════╝ ╚═╝  ╚═══╝╚═╝  ╚═╝

█████╗  ██████╗ ███████╗███╗   ██╗████████╗
██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝
███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║
██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║
██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║
╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝
```

# LUNA

**A personal work assistant for reading and editing documents and files.**

You talk to LUNA in plain language — "find where the price is set in this report and change it to
42" — and it does the work by calling tools: reading files, searching inside large JSON documents,
applying edits. Under the hood it is a **thin harness around one LLM agent**. There is no workflow
engine and no database: the agent decides each next step by itself, and all state lives as plain
files on disk. You use it through a **command-line REPL** or a **web UI**, both backed by the same
agent core.

## How to run

You need **Python 3.11** and a reachable LLM provider (configured in `.env`). From the repo root:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .[dev]
copy .env.example .env    # then fill in your LLM endpoint
```

Then pick an interface:

```powershell
# Command-line assistant (talks to the agent directly, no server)
python -m cli            # or just: luna

# HTTP server + built-in web UI at http://localhost:8000
luna-web                # or: uvicorn core.app.server:create_app --factory --host 0.0.0.0 --port 8000
```

The CLI needs only a working LLM provider. The server additionally serves the web UI. Both keep
their files under `~/.luna/`.

With Docker:

```powershell
cd docker && docker compose up --build    # web UI on localhost:8000
```

## Choosing an LLM provider

LUNA talks to any OpenAI-compatible model, picked with the `LLM_PROVIDER` variable in `.env`:

- `openrouter` — a public gateway to many models under one key
- `gpustack` — vLLM served through GPUStack (Responses API)
- `ollama` — a local Ollama instance

## Documentation

Full documentation lives in [`docs/`](docs/README.md) — start with
[`docs/architecture.md`](docs/architecture.md) for the big picture, then the runtime, storage,
tools, modes, and interface guides. It is written as plain narrative so you can read it and build
a mental model of how LUNA works.
