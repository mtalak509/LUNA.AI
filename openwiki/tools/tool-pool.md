---
type: subsystem
title: The flat tool pool and per-agent slices
description: How LUNA defines one shared pool of tools, tags each tool with declarative attributes, computes each agent's slice by subtraction, gates optional subsystems with feature flags, and gives tools access to process services.
tags: [tools, tool-pool, feature-flags, agents, extension]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T17:46:46.267Z
sources:
  - id: openwiki-source-9cae2963ffbc49ec962d3395
    resource: repo://core/agent/bootstrap.py
  - id: openwiki-source-8663d578d4a41ad0e6edc1c1
    resource: repo://core/agent/pool.py
  - id: openwiki-source-e3f88dd0af219ff6f6c2decf
    resource: repo://core/agent/tools/__init__.py
  - id: openwiki-source-8bbed1e3cb44f8f7104d0888
    resource: repo://core/agent/tools/_clients.py
  - id: openwiki-source-23ea0cffc72ce040b46eb998
    resource: repo://core/agent/tools/_hitl.py
  - id: openwiki-source-77582883137c489d92067600
    resource: repo://core/agent/tools/_subagents.py
  - id: openwiki-source-277976f7cb0a0aa61d455571
    resource: repo://core/agent/tools/delegation_tools.py
  - id: openwiki-source-2612040bf3e381d14b3999d3
    resource: repo://core/agent/tools/rag_tools.py
  - id: openwiki-source-27ee44b17a928f4d359d8b25
    resource: repo://core/config.py
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
---

# The flat tool pool and per-agent slices

LUNA has **one** list of tools shared by every agent. There are no per-agent tool registries or
whitelists. What a given agent may use is worked out by **subtraction** from that list, based on
attributes attached to each tool. This follows from the idea that a specialist is defined by its
prompt, not by a private toolbox.

## The pool

`FULL_POOL` in `core/agent/pool.py` is the single source for production and development:

| Group | Tools | Attributes |
|---|---|---|
| Files | `read`, `ls`, `find`, `grep` | read-only |
|  | `write`, `edit`, `rm` | `is_write` |
| Knowledge base | `knowledge_base_search`, `knowledge_base_get_reference` | `feature="rag"` |
| Delegation | `delegate_to_subagent` | `main_only` |
| Questions | `ask_user`, `select_from_options` | none |

The tools themselves live in `core/agent/tools/` (see [file tools](file-tools.md),
[RAG](../integrations/rag-knowledge-base.md), [delegation](../workflows/delegation-and-subagents.md)
and [modes and HITL](../workflows/modes-and-hitl.md)).

## Declaring a tool: `agent_tool`

`agent_tool` (`core/agent/tools/__init__.py`) is a thin wrapper over LangChain's `@tool`. It
builds the tool normally and then merges four attributes into `tool.metadata`:

- **`is_write`** — the tool changes something. Plan mode hides and blocks it, Confirm mode asks
  before running it.
- **`main_only`** — only the main agent may have it. Subagents lose it, which is how they are
  kept from delegating further.
- **`fs_scope`** — the filesystem zone the tool is tied to, or `None` for tools not tied to the
  filesystem.
- **`feature`** — the optional subsystem the tool belongs to, or `None` for core tools.

It supports the same forms as `@tool`: bare `@agent_tool`, `@agent_tool(is_write=True)`, and a
custom name as the first argument. The reader functions `is_write_tool`, `is_main_only`,
`fs_scope_of` and `feature_of` are the single place these keys are read; they return defaults for
tools without LUNA metadata, so third-party tools can sit in the pool safely.

What the model actually sees about a tool is its name, its docstring (verbatim, as the
description) and its argument schema. `scripts/dump_tool_schemas.py` prints exactly that, and can
apply a slice for a given scope and mode.

## Computing a slice: `assemble_pool`

`assemble_pool(full_pool, agent_scope=..., permission_mode=None, disabled_features=...)` keeps a
tool unless one of these subtractions removes it:

1. its `feature` is in `disabled_features`;
2. the agent is a subagent and the tool is `main_only`;
3. its `fs_scope` is set and is not one of the zones allowed for the scope
   (`main`: `workspace`, `procedures`; `subagent`: `subagent`, `procedures`);
4. `permission_mode == "plan"` and the tool is a write tool.

It is called when an agent is built: `build_session_agent` for the main agent and
`SubagentFactory.build` for subagents, both with `disabled_features` from config and **without** a
permission mode. Plan mode is not applied here because the graph is built once while the mode
changes per turn; the per-turn filter is `PermissionMiddleware` (see
[middleware](../architecture/middleware.md)). The optional `permission_mode` parameter exists for
building a starting pool for a fixed mode.

None of the current tools sets an `fs_scope` string (the file tools resolve their zone at call
time through `PathScope`), so rule 3 is a seam for future zone-bound tools rather than an active
filter.

## Feature flags

An optional subsystem can be switched off without removing its wiring. `InfraConfig` exposes
`disabled_features`; today it contains `rag` unless `RAG_ENABLED=true`. Tools tagged with that
feature are subtracted from every slice, and the subsystem's clients are not even built (see
[RAG](../integrations/rag-knowledge-base.md)). Adding a flag means adding a config field, putting
its name into `disabled_features`, and tagging the tools with `feature=`.

## How tools reach process services

Tools run inside the graph and cannot receive arbitrary objects as arguments. Per-turn data
(workspace path, scope, modes, session id) comes from the runtime context via `get_runtime()`.
Long-lived process services come from three module-level holders installed once by
`init_process`:

| Holder | Getter | Used by |
|---|---|---|
| `_clients.py` | `get_clients()` | RAG tools |
| `_subagents.py` | `get_subagent_factory()` | `delegate_to_subagent` |
| `_hitl.py` | `get_hitl_registry()` | question tools, Confirm gate, `/hitl/respond` |

Each getter raises a `ToolException` if its holder was never set, so a wiring mistake shows up
as a readable tool error. Holders carry no per-session state.

## Errors are feedback

A tool signals an expected problem by raising a `ToolException` subclass (`FileAccessError`,
`DelegationError`, `HitlToolsError`, `RagError`, …). The outermost middleware turns any
exception into an error `ToolMessage` so the model can correct its arguments and retry; only
non-`ToolException` errors are logged as bugs.

## Adding a tool

1. Write an async function with a clear docstring (it is the model's description) and typed
   arguments.
2. Decorate it with `@agent_tool(...)` and the right attributes. For a write tool add
   `@with_checkpoint()` inside it; for large text output add `@truncate_output("head" | "tail")`
   inside it.
3. Read session data from `get_runtime().context` and paths through `PathScope`.
4. Add it to `FULL_POOL` in `core/agent/pool.py`.
5. Describe it in the main prompt's `<tools>` section (see
   [prompts](../project/prompts-and-procedures.md)), add unit tests, and add a live scenario if it
   changes agent behaviour.
