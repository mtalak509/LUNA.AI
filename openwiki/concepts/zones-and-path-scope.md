---
type: concept
title: Filesystem zones, PathScope and attachments
description: How LUNA isolates agents by resolving every file path through PathScope — who may read and write where, the hidden service folders — and how WorkspaceManager applies the same policy to user uploads in attachments/.
tags: [security, filesystem, zones, attachments, path-scope]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T17:46:46.267Z
sources:
  - id: openwiki-source-e1f3d5a721b9517f9aa6ba09
    resource: repo://core/agent/tools/files_tools.py
  - id: openwiki-source-bc779d44eeaa18d27c90f893
    resource: repo://core/agent/tools/fs_paths.py
  - id: openwiki-source-a1954481bf636161937b8062
    resource: repo://core/agent/workspace.py
  - id: openwiki-source-43a593a2b71e024f51c42fdd
    resource: repo://tests/agent/test_fs_paths.py
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
---

# Filesystem zones, PathScope and attachments

LUNA runs the main agent and its subagents in one process. Their boundary is therefore not a
process or container; it is **which paths each agent can resolve**. Every file tool turns the
model's relative path into an absolute one through `PathScope`
(`core/agent/tools/fs_paths.py`), and that resolver is where isolation actually lives. The
user-facing side (HTTP uploads and the CLI `/attach`) goes through `WorkspaceManager`
(`core/agent/workspace.py`), which reuses the same resolver.

## Zones

A session folder has two kinds of agent zone, side by side:

- `workspace/` — the main agent's zone, holding the user's documents;
- `subagents/<uid>/` — one zone per delegated subagent.

| Path the agent asks for | Main agent | Subagent |
|---|---|---|
| a plain relative path | read + write in `workspace/` | read + write in its own zone |
| `workspace/...` | (just a folder named `workspace` inside its zone) | **read only** view of the main `workspace/` |
| `subagents/...` | read + write in `<session>/subagents/` (to collect results or clean up) | (just a folder inside its zone) |
| `.runtime/...` at a zone root | refused | refused |

The two prefixes are symmetric: each one is an explicit address of the *neighbouring* zone. A
subagent that tries to **write** through `workspace/` gets
"workspace/ доступен субагенту только на чтение". This is the mechanism behind "a subagent
advises, the main agent edits" (see [delegation](../workflows/delegation-and-subagents.md)).

`PathScope` is built per call from the runtime context (`PathScope.from_context`), using
`agent_scope`, `workspace_path` and `subagent_path`. A subagent scope without a `subagent_path`
is a construction error. The `fs_scope` attribute that `agent_tool` supports is not set on the
file tools; their zone comes entirely from this runtime resolution.

## The safety checks

`_safe_join(root, rel)` enforces, in order:

1. the path is not empty and not absolute;
2. after joining and `resolve()` (which collapses `..` and follows symlinks), the target is still
   inside `root` — otherwise "Путь вне зоны агента";
3. the first segment inside the root is not `.runtime`.

Checks 2 and 3 run on the normalized path, so `notes/../.runtime/messages.jsonl` and
`subagents/../workspace/.runtime/...` are both refused. Only `.runtime` directly under the zone
root is blocked; a nested folder that happens to be called `.runtime` is an ordinary folder.
Through the `subagents/` prefix the root is the whole `subagents/` folder, so a subagent's own
`.runtime/` is not covered by check 3 for the main agent (subagents keep no log there today).

Every refusal is a `FileAccessError`, a `ToolException`. The tool-error middleware hands it back
to the model as an error result, so a bad path is a correction, not a crash.

## Hidden service folders

Two folder names are service space rather than working files:

- `.runtime/` — the conversation log, blobs and the pending-question marker. Unreachable as
  described above.
- `.truncated_outputs/` (`SPILL_DIR`) — full text of tool output that was cut (see
  [file tools](../tools/file-tools.md)). `read` may open it, but `resolve_write` refuses any path
  with that segment, so `write`, `edit` and `rm` cannot touch it.

Both are in `HIDDEN_DIRS`, which `ls`, `find` and `grep` skip, so a search never finds its own
earlier output. `PathScope.spill_dir` is the single place that decides where spills go: the
agent's own read-write root (the workspace for main, the zone for a subagent, since a subagent
cannot write to `workspace/`).

## User-side files: `WorkspaceManager`

The user also writes into the workspace — attaching a file in the web UI or with `/attach`.
Those writes bypass the agent's tools, so `WorkspaceManager` exists to make them follow the same
rules. It is shared by the HTTP routes and the CLI so that an undo scenario tried in the REPL
says something about the server too.

Its `resolve` runs `PathScope(agent_scope="main").resolve_write` and adds three narrowings:

- **absolute paths are refused early**, before any prefixing could hide the absoluteness;
- **drive letters and UNC roots are refused**, because `PathScope` reads paths as POSIX, where
  `C:/x` counts as relative and would quietly land at the zone root;
- **dotfile names are refused** — a user file is not runtime infrastructure.

It also confines the result to the zone, which removes the `subagents/` prefix the main agent
is normally allowed. Backslashes are normalized to `/`.

Writes refuse to overwrite unless asked (`FileConflictError`), refuse content over
`MAX_UPLOAD_BYTES` (20 MB, `FileTooLargeError`), create parent folders, and take a checkpoint
after each write or delete (see [checkpoints](checkpoints-and-undo.md)).

## Attachments are a directory, not a log

Everything the user attaches goes into one folder, `workspace/attachments/`, and nowhere else.
`resolve_attachment` prefixes the path with `attachments/` and refuses anything that resolves
outside it.

The point is provenance by location. The agent learns which files the user gave it from a
listing of that directory taken at each model call (`list_attachments`, used by the context
injection middleware — see [middleware](../architecture/middleware.md)). There is no journal of
uploads that could disagree with the disk: a deleted file simply stops being listed, and an
empty directory is reported as empty. The main prompt tells the agent not to write into
`attachments/` itself and to put its own results in `artifacts/`.

`attach_from_host` (used by `/attach`) checks the source file's size with `stat` before reading
it, so an oversized file is never loaded into memory. The HTTP upload route reads at most
`MAX_UPLOAD_BYTES + 1` bytes for the same reason.
