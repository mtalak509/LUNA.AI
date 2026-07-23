# LangChain and LangGraph

LUNA is built on **LangChain v1.x** and **LangGraph 1.x**. This document explains how the design
maps onto those libraries — and, just as importantly, which parts of them LUNA deliberately does
*not* use. The theme is: take LangChain as a box of building blocks, but keep the source of truth
on the filesystem, not inside the framework.

## What LUNA uses

**`create_agent` — the agent loop.** Each agent (main or subagent) is one `create_agent` instance.
It provides the think-act loop, tool calling, middleware hooks, and streaming out of the box.
LUNA does *not* wrap it in any higher-level orchestration graph — that would contradict "the agent
drives itself" (idea #1 in [architecture.md](architecture.md)). The wrapper class is `BaseAgent`
([core/agent/base.py](../core/agent/base.py)).

**Middleware.** LangChain's middleware hooks (`wrap_model_call`, `wrap_tool_call`) are how LUNA
adds its cross-cutting behavior: the Plan/Act permission filter, the Confirm gate, context
injection, and tool-error handling. See [runtime.md](runtime.md). This is the main extension point
LUNA leans on.

**Tools.** Plain LangChain `@tool` functions (wrapped by a thin `agent_tool` decorator that adds
LUNA's filtering attributes). One flat set for everyone; each agent's slice is computed by
subtraction. See [tools.md](tools.md).

**Chat models.** One OpenAI-compatible client (`ChatOpenAI`) for all three providers
(OpenRouter / GPUStack / Ollama). The provider differences are just the endpoint and API style.

**Streaming.** LangGraph's streaming (`astream` with `messages` / `updates` / `custom` modes,
plus `get_stream_writer()` for custom events from tools) is what produces the live event stream.
When the main agent delegates, it consumes the subagent's stream and re-emits those events into
its own, tagged with the subagent's namespace — all within one process. See [runtime.md](runtime.md).

**In-process delegation.** A subagent is launched from inside the `delegate_to_subagent` tool as an
`asyncio` task in the same event loop — not a subprocess. Because everything shares one process,
parallel subagents would just be `asyncio.gather`, tracing nests automatically, and a paused
question is a suspended coroutine. No inter-process plumbing.

## What LUNA deliberately does not use

**No orchestration graph over the agent.** No `StateGraph` with `classify` / `route` / `plan`
nodes in front of the agent. The route is the agent's own sequence of tool calls, not edges in a
graph. Delegation is a *tool call*, not a graph edge — so LangGraph's `subgraphs=True` isn't used
either; the main agent multiplexes subagent events explicitly.

**No LangGraph checkpointer.** The graph is built once and is stateless between turns; LUNA carries
the conversation itself and mirrors it to a file. The source of truth is the filesystem, and a
checkpointer would introduce a *second* source of truth (graph state vs. disk) that could drift
from it. Undo is a filesystem snapshot, not a saved graph state. So `langgraph-checkpoint-*` is not
a dependency.

**No `interrupt` / `resume` for questions.** LangChain's interrupt mechanism needs a checkpointer
and re-runs the interrupted node on resume — unsafe for a tool node with side effects. Instead, an
agent question is a coroutine awaiting an `asyncio.Future`: it suspends at the exact spot and
resumes there, no re-execution, no saver. See [modes-and-hitl.md](modes-and-hitl.md).

**No built-in summarization middleware.** Because the graph is stateless and LUNA replays the whole
history each turn, the built-in summarizer would re-summarize every turn. LUNA's own compactor
summarizes once and reuses the result (see [runtime.md](runtime.md)).

**No `langchain-ollama` / custom model clients.** Everything goes through `ChatOpenAI`.

## A note on the model backend

The whole agent design rests on tool calling, and when running against self-hosted vLLM (via
GPUStack) that depends on the inference server being configured for tool calls (auto tool choice +
the right tool-call parser for the model). One practical wrinkle LUNA had to patch: streaming of
the model's "reasoning" tokens isn't handled by the stock `ChatOpenAI` for vLLM's event shape, so
there's a small patch that installs at import time to recover them for the live UI. That history is
recorded as TD-1 in [tech-debt.md](tech-debt.md).

## Next

- The loop and middleware these libraries power: [runtime.md](runtime.md)
- The big picture the mapping serves: [architecture.md](architecture.md)
