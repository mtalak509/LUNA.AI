---
type: integration
title: Optional RAG knowledge base
description: The optional two-step knowledge-base search over Qdrant — tools, feature flag, the RAGService/EmbeddingService/QdrantService layers, and the HTTP client retry and error taxonomy underneath.
tags: [rag, qdrant, embeddings, http-client, feature-flag]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T17:46:46.267Z
sources:
  - id: openwiki-source-8bbed1e3cb44f8f7104d0888
    resource: repo://core/agent/tools/_clients.py
  - id: openwiki-source-2612040bf3e381d14b3999d3
    resource: repo://core/agent/tools/rag_tools.py
  - id: openwiki-source-3420ba3218b27710cf4de457
    resource: repo://core/clients/base.py
  - id: openwiki-source-0d87793e0870bd5e59cbc7b3
    resource: repo://core/clients/errors.py
  - id: openwiki-source-6b210a309525fffaef25886b
    resource: repo://core/services/embedding_service.py
  - id: openwiki-source-898be2711feaf346f2303692
    resource: repo://core/services/rag_service.py
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
---

# Optional RAG knowledge base

LUNA can give its agents semantic search over an external knowledge base stored in Qdrant. The
feature is **off by default** (`RAG_ENABLED=false`). When it is off, nothing RAG-related is
built and the two tools are removed from every agent's tool slice; the wiring stays in the code
so turning it on is a configuration change.

## The two tools

Both live in `core/agent/tools/rag_tools.py`, are read-only, are available to main agent and
subagents, and carry `feature="rag"`. The collection name `knowledge_base` is fixed inside the
tools rather than passed by the model.

| Tool | Returns |
|---|---|
| `knowledge_base_search(query, top_k=5)` | ranked previews: `id`, `score`, and only the `description` and `tags` payload fields; or the text "Ничего не найдено." when there are no hits |
| `knowledge_base_get_reference(reference_id)` | the full point: `{"id", **payload}` |

The split is deliberate: full reference texts are large, so search returns compact previews and
the model fetches one document by id only when it has picked it. An empty search is a normal
result, not an error. A missing id raises `RagError`.

Network failures from the services arrive as `UpstreamError`; the tools wrap them in `RagError`
(a `ToolException`), so the model sees a tool error and can retry or move on instead of the turn
crashing (see [middleware](../architecture/middleware.md)).

## How the tools reach the services

Tools do not receive services through the graph. At process start `init_process` calls
`set_clients(from_config(cfg))` (`core/agent/tools/_clients.py`), and a tool fetches the live
services with `get_clients()`. If RAG is disabled, `from_config` returns `Clients(rag=None)`
without constructing anything; since the tools are subtracted from the pool in that mode, nobody
touches `rag`. Calling `get_clients()` before `set_clients` raises a `ToolException`.

## The service layers

`core/services/` is free of LangChain and LangGraph.

**`RAGService`** glues two steps together: embed the query, then search a collection.
`search` returns Qdrant hits as `{"id", "score", **payload}` sorted by score; `get_reference`
returns one point or `None`; `find_by_field` does an exact payload lookup without vectors.

**`EmbeddingService`** turns text into vectors by calling a remote OpenAI-compatible
`POST /embeddings` endpoint configured by `EMBEDDING_*`. This endpoint is separate from the LLM
provider and has its own URL and key. It keeps an in-memory `text → vector` cache, sends only
unique cache misses upstream, and reorders the response by `index` in case the server returns
items out of order. An answer without `data` is an `UpstreamBadResponse`. There is no local
embedding model: `fastembed` is still listed as a dependency (and the Dockerfile installs
`libgomp1` for it), but no code imports it.

**`QdrantService`** wraps `AsyncQdrantClient`: vector search via `query_points` with payload,
fetch by id, exact field lookup, and paging through all points. Point ids are returned as
strings. The client is built from `QDRANT_HOST`, `QDRANT_PORT`, `QDRANT_USE_SSL` and
`QDRANT_PREFIX`.

## HTTP transport and errors

`EmbeddingService` inherits from `BaseHTTPClient` (`core/clients/base.py`), the shared transport
for raw HTTP clients:

- the `httpx.AsyncClient` is created lazily in the current event loop and closed by `aclose`;
- an API key becomes a `Bearer` header;
- requests are retried with tenacity, exponential backoff and `HTTP_RETRIES` attempts, but
  **only** for transient errors;
- the response is parsed as JSON once, after success.

The error taxonomy in `core/clients/errors.py` decides what is transient:

| Class | Cause | Retried |
|---|---|---|
| `UpstreamTimeout` | `httpx.TimeoutException` | yes |
| `UpstreamConnectionError` | other `httpx.TransportError` | yes |
| `UpstreamServerError` | HTTP 5xx | yes |
| `UpstreamAuthError` | 401 / 403 | no |
| `UpstreamNotFound` | 404 | no |
| `UpstreamBadResponse` | other 4xx, or a non-JSON body | no |

All of them carry `status_code`, `url` and `service` for logs and error messages.

## Enabling it

Set `RAG_ENABLED=true`, point `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` at an embeddings server
and `QDRANT_*` at a Qdrant instance that already has a `knowledge_base` collection whose points
carry `description` and `tags` payload fields. LUNA does not ingest documents into Qdrant; it
only reads. See [LLM providers and configuration](llm-providers-and-config.md) for the
configuration groups and [the tool pool](../tools/tool-pool.md) for how feature flags subtract
tools.
