---
type: integration
title: LLM providers and configuration
description: How LUNA reads its configuration from the environment and .env, how LLM_PROVIDER selects between OpenRouter, GPUStack/vLLM and Ollama through one ChatOpenAI client, and the reasoning-stream patch for vLLM.
tags: [configuration, llm, providers, openai, vllm]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T17:46:46.267Z
sources:
  - id: openwiki-source-364a89a77ff1b7418dc1c6c3
    resource: repo://core/agent/base.py
  - id: openwiki-source-bddb0b8dc08e88549b6fd6f0
    resource: repo://core/agent/patches/langchain_openai_reasoning.py
  - id: openwiki-source-5a1154d06cefdb83d986ffbc
    resource: repo://core/app/sse.py
  - id: openwiki-source-27ee44b17a928f4d359d8b25
    resource: repo://core/config.py
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
---

# LLM providers and configuration

## Configuration

All infrastructure settings come from `core/config.py`. `InfraConfig` is a pydantic-settings
object composed of groups, each reading its own environment prefix:

| Group | Prefix | What it holds |
|---|---|---|
| `server` | none (`HOST`, `PORT`, `RELOAD`, `ALLOWED_ORIGINS`, `ROOT_PATH`) | HTTP server binding, CORS origins, ingress prefix |
| `logging` | `LOG_` | level, file logging |
| `gpustack` | `GPUSTACK_` | base URL, key, model, timeout, retries |
| `openrouter` | `OPENROUTER_` | same fields for OpenRouter |
| `ollama` | `OLLAMA_` | same plus embedding fields |
| `embedding` | `EMBEDDING_` | the separate embeddings endpoint (RAG) |
| `qdrant` | `QDRANT_` | vector store address (RAG) |
| `http` | `HTTP_` | timeout / retries / backoff defaults for `BaseHTTPClient` |

Two top-level fields select behaviour:

- `LLM_PROVIDER` — `gpustack`, `openrouter` or `ollama`. **The code default is `openrouter`.**
  It affects only the chat model; the embeddings endpoint is configured independently.
- `RAG_ENABLED` — defaults to `false`. When false, the `disabled_features` property contains
  `rag`, and the knowledge-base tools are subtracted from every agent's tool slice (see
  [tool pool](../tools/tool-pool.md)).

Practical details:

- The `.env` file is resolved relative to the repository root (computed from the location of
  `config.py`), not the current directory, so running from another folder does not silently fall
  back to defaults. Real environment variables take precedence over `.env`.
- Unknown variables are ignored, names are case-insensitive, and base URLs have trailing
  slashes stripped.
- `ALLOWED_ORIGINS` accepts a comma-separated string (`*` or `a,b,c`) instead of JSON.
- `get_config()` is cached, so the environment is read once per process.
- The session root is **not** an environment setting: it is fixed at `~/.luna/sessions` (see
  [sessions and storage](../concepts/sessions-and-storage.md)).

`.env.example` lists every variable with a comment. `EVAL_JUDGE_MODEL` there is read only by the
live eval suite (see [tests and evals](../testing/tests-and-live-evals.md)).

## One client for three providers

`build_chat_model(cfg)` in `core/agent/base.py` returns a LangChain `ChatOpenAI` for every
provider. All three speak an OpenAI-compatible protocol; they differ only in endpoint and API
style:

| `LLM_PROVIDER` | Endpoint | API style |
|---|---|---|
| `openrouter` | `OPENROUTER_BASE_URL` (public gateway) | Chat Completions |
| `ollama` | `OLLAMA_BASE_URL` with `/v1` appended if missing | Chat Completions |
| `gpustack` | `GPUSTACK_BASE_URL` (vLLM behind GPUStack) | **Responses API** (`use_responses_api=True`, `output_version="responses/v1"`) |

All are built with `temperature=0`, the group's timeout and retry count, and `"EMPTY"` as the
key when none is configured (local servers accept any key). The same function builds the model
for the summarizer used by history compaction. No provider-specific packages
(`langchain-ollama` and the like) are used.

The whole design rests on tool calling, so a self-hosted vLLM must be started with automatic
tool choice and the right tool-call parser for the model.

## The vLLM reasoning patch

Over the Responses API, vLLM streams the model's reasoning as `response.reasoning_text.delta` /
`.done` events. Stock `langchain-openai` only understands OpenAI's
`response.reasoning_summary_*` events and drops the vLLM ones, so reasoning never reached the
stream.

`install_reasoning_stream_patch` (`core/agent/patches/langchain_openai_reasoning.py`) wraps the
module-level converter `_convert_responses_chunk_to_generation_chunk` in `langchain-openai`:

- vLLM reasoning deltas become `AIMessageChunk`s carrying a `reasoning` content block in the same
  shape a non-streaming response has, so LangChain's chunk aggregation assembles the full
  reasoning in the final message;
- `.done` events produce an empty block to mark the boundary instead of repeating the full text;
- every other event goes to the original converter unchanged;
- installation is idempotent (a flag on the wrapper).

It is installed when `core.agent.base` is imported, before any model is built. The patch only
makes reasoning **visible**; `BaseAgent` still strips reasoning before anything is stored (see
[a turn inside BaseAgent](../architecture/agent-turn.md)). The SSE layer exposes it to the UI as
a `reasoning` field in `messages` frames.

## When the model is not there

`_is_model_unavailable_error` recognizes a backend that is down or has not loaded the model:
an `openai.APIConnectionError`, or an error with status 404 whose text contains
"model not found" or "no running instances available". A turn that hits it ends with an `error`
event carrying `code: "model_unavailable"` instead of a traceback.

## Diagnostic scripts

- `scripts/inspect_chat_model.py` builds the model through `build_chat_model` (so the patch is
  active), sends one prompt and prints what each stream chunk and the aggregated message contain;
  `--raw` dumps every chunk, `--invoke` adds a non-streaming call for comparison.
- `scripts/list_gpustack_models.py` lists models available on the configured GPUStack.
- `scripts/dump_tool_schemas.py` prints the tools exactly as they are sent to the model.
