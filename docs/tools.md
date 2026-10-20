# Tools

Tools are how the agent gets anything done — every action it takes is a tool call. LUNA has
**one flat set of tools** shared by every agent; there are no per-agent tool lists. What an
individual agent can use is worked out by *subtraction* from that one set. This document lists the
tools and explains the subtraction.

The set is defined in [core/agent/pool.py](../core/agent/pool.py); the tools themselves live in
[core/agent/tools/](../core/agent/tools/).

## The tools

### Working with files

LUNA works with the files you bring: notes, reports, data, anything stored as text. Seven
ordinary file tools cover that work, scoped to the agent's zone (see [storage.md](storage.md)).
Paths are always relative.

| Tool | What it does |
|---|---|
| `read` | Read a text file. With `offset` and `limit` it returns only a window of lines, headed by "lines X–Y of N". |
| `write` | Create a file or overwrite it whole. |
| `edit` | Replace an exact piece of text in a file (must match once, unless `replace_all`). |
| `ls` | List one directory. |
| `find` | Find files by name with a glob pattern, like `*.md` or `**/*.md`. |
| `grep` | Find lines by content with a regular expression. Each hit is `path:line: text`. |
| `rm` | Delete a file. |

They mirror the Read/Write/Edit/Glob/Grep tools that coding agents use, so models already know
them. But they are real tools rather than shell commands, which is what lets LUNA attach
zone-scoping and checkpoints to them.

Large files never need to go into the context whole. The usual flow is: `grep` for the place you
need, `read` a window of lines around the line number it reports, then `edit` exactly that piece.
A JSON or CSV file is just another text file here: the same three steps apply.

### Knowledge base (optional)

If a knowledge base is configured, two tools give the agent semantic search over it, in two steps:

| Tool | What it does |
|---|---|
| `knowledge_base_search` | Semantic search; returns short previews (id, score, description, tags). |
| `knowledge_base_get_reference` | Fetch the full text of one reference by its id. |

Search first, pick the relevant id, then fetch — so full texts are only pulled when actually
needed. This whole feature is **off by default** (`RAG_ENABLED=false`); when off, the tools are
simply absent from the set (see "Feature flags" below). It's backed by Qdrant for vectors.

### Asking you a question

Sometimes the agent needs input from you mid-turn. Two tools pause the turn and wait for your
answer:

| Tool | What it does |
|---|---|
| `ask_user` | Ask an open question and wait for a free-text answer. |
| `select_from_options` | Offer a short list of choices (built only from earlier tool results) and wait for a pick. |

These are covered in full in [modes-and-hitl.md](modes-and-hitl.md), including how a subagent's
question is routed up through the main agent.

### Delegating to a subagent

| Tool | What it does |
|---|---|
| `delegate_to_subagent` | Hand a self-contained sub-task to a specialist subagent and get back `{summary, artifacts}`. |

The subagent runs with a cold context (it sees only the task text you write), works in its own
folder, and returns a summary plus the list of files it produced. It never edits the main
documents. Only the main agent has this tool. See [architecture.md](architecture.md) for the
model and [runtime.md](runtime.md) for how its events stream back.

## How each agent gets its slice

Every tool carries a few declarative attributes (attached by the `agent_tool` decorator in
[core/agent/tools/__init__.py](../core/agent/tools/__init__.py)). When LUNA assembles the tool set
for a specific agent, it starts from the full set and **subtracts** based on those attributes:

- **`is_write`** — the tool changes something. In **Plan** mode these are removed so the model only
  reads. (Write tools are also the ones that trigger a checkpoint and the Confirm gate.)
- **`main_only`** — the tool belongs to the main agent only. `delegate_to_subagent` is marked this
  way, so a subagent can't delegate further.
- **`fs_scope`** — which filesystem zone the tool works in. A tool tied to a zone the agent can't
  reach is dropped. (Tools not tied to the filesystem, like the knowledge base, are always
  available.)
- **`feature`** — which optional subsystem the tool belongs to. If that subsystem is turned off,
  the tool is dropped.

This "one set, subtract per agent" approach is a direct consequence of idea #2 from
[architecture.md](architecture.md): tools are shared, and a specialist is defined by its prompt,
not by a private toolbox. Adding a tool means adding it to the one set in `pool.py`.

## Feature flags

Some subsystems can be switched off without ripping out their wiring. The knowledge base is the
current example: with `RAG_ENABLED=false` (the default), the RAG tools are marked with a `feature`
of `rag`, that feature lands in the "disabled" set, and those tools are subtracted when the set is
assembled. The code stays in place; only the tools disappear from what the model sees.

## Errors are self-correction, not crashes

When a tool hits an expected problem — a missing file, a bad regular expression, an `edit`
whose text doesn't match — it raises a clean error that becomes an "error result" message back to
the model, which then fixes its arguments and retries. This is handled by the tool-error
middleware ([runtime.md](runtime.md)) and is why a wrong guess is a nudge, not a dead turn. Real
bugs still raise loudly and get logged with a trace.

## Next

- The zones the file tools operate in: [storage.md](storage.md)
- Plan/Act, Confirm, and the question tools: [modes-and-hitl.md](modes-and-hitl.md)
