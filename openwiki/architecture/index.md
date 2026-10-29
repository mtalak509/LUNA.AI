# Files

- [A turn inside BaseAgent](agent-turn.md) - How BaseAgent.run_stream runs one turn of the think-act loop — history ownership without a checkpointer, compaction, streaming modes, what gets persisted, and how errors end a turn.
- [Conversation history and compaction](history-and-compaction.md) - How the main agent's conversation is kept as an append-only messages.jsonl log with summary markers, and how HistoryCompactor shrinks long histories once without orphaning tool results.
- [Middleware stack](middleware.md) - The four LangChain middleware LUNA wraps around every model and tool call — tool-error conversion, Plan/Act permission, Confirm gate, and working-context injection — their order and exact behaviour.
- [Architecture overview](overview.md) - The big picture of LUNA — a thin harness around one self-driving LangChain agent, a flat shared tool pool, filesystem-first state with no database or checkpointer, in-process subagents, and two front ends over one core.
