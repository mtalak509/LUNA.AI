# 002. Truncate tool output with a decorator

- id: 002
- status: accepted
- source: digests/1.2.0, digests/1.2.4
- value: high

## Why

LUNA has no limit on tool output. `read_file` returns the whole file. A large `read` or
`grep` lands in the model context in full. The parent hit this in 1.2.0 and cut oversized
tool results so search and file reads could not blow the window.

The 1.2.0 cut still picked head or tail from a whitelist of tool names. A new search tool
silently got `tail` and lost the start of its output. 1.2.4 removed that whitelist and
declared the strategy on the tool. LUNA takes that contract, not the whitelist.

## What changes

A decorator on the tool truncates that tool's output. The decorator declares the strategy,
`head` or `tail`. Nothing infers the strategy from the tool name.

The cut is on the order of 50 KB or 2000 lines, matching the parent. The full output is
written to `workspace/.truncated_outputs/`. The truncated result tells the model where that
file is, so it can read the rest with the file tools.

`read` and `grep` from [001](001-file-tools-replace-json.md) are the tools this exists for.
The same decorator is available to any later tool that can return a large string.

## Why this is LUNA

The agent keeps working on large files without a second document format. The spill file is
ordinary session state on disk, in the same filesystem-first layout as the rest of
`workspace/`. The model recovers the cut text by reading a path, not by a special tool.

## Out of scope

- The 1.2.0 name whitelist.
- Per-tool ad hoc caps inside `search_files` (100 hits, 200-character lines, skip files over
  1 MB). Those go away with the tool split in card 001; the decorator is the single limit.
- Sandbox, `bash`, and the WebSocket transport that shipped in the same 1.2.0 release.
