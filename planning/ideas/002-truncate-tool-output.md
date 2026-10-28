# 002. Truncate tool output with a decorator

- id: 002
- status: building
- source: digests/1.2.0, digests/1.2.4
- value: high

## Why

LUNA has no limit on tool output. `read_file` returns the whole file. A large `read` or
`grep` lands in the model context in full. Search and file reads must not blow the window.

The strategy is declared on the tool. A whitelist of tool names is the wrong place for it:
a new search tool would silently keep the tail and lose the start of its output.

## What changes

A decorator on the tool truncates that tool's output. The decorator declares the strategy,
`head` or `tail`. Nothing infers the strategy from the tool name.

The cut is 2000 lines or 50 KB, whichever comes first. The full output is written to
`.truncated_outputs/` in the zone of the agent that called the tool: `workspace/` for the main
agent, its own zone for a subagent, which cannot write to `workspace/`. The truncated result
tells the model where that file is, so it can read the rest with the file tools. The folder is
readable by `read` and hidden from `ls`, `find` and `grep`, so a search never finds its own
earlier output. Its location is decided in one place, `PathScope.spill_dir`.

`read` does not spill: its source is already a file on disk. Its note points to
`offset`/`limit` instead.

`read` and `grep` from [001](001-file-tools-replace-json.md) are the tools this exists for.
The same decorator is available to any later tool that can return a large string.

## Why this is LUNA

The agent keeps working on large files without a second document format. The spill file is
ordinary session state on disk, in the same filesystem-first layout as the rest of
`workspace/`. The model recovers the cut text by reading a path, not by a special tool.

## Out of scope

- Choosing `head` or `tail` from a tool-name whitelist.
- Per-tool ad hoc caps inside `search_files` (100 hits, 200-character lines, skip files over
  1 MB). Those go away with the tool split in card 001; the decorator is the single limit.
- A process sandbox and a change of transport.
