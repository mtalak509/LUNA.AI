---
type: concept
title: Checkpoints and undo
description: How LUNA's undo works as full copies of the workspace folder — when snapshots are taken, how numbering stays monotonic across restores, and how history is reloaded after a restore.
tags: [checkpoints, undo, storage, filesystem, history]
sources:
  - id: openwiki-source-0999e357957975c8879ee999
    resource: repo://cli/commands.py
  - id: openwiki-source-70210339a3af34349c2062c6
    resource: repo://core/agent/checkpoint.py
  - id: openwiki-source-277976f7cb0a0aa61d455571
    resource: repo://core/agent/tools/delegation_tools.py
  - id: openwiki-source-a1954481bf636161937b8062
    resource: repo://core/agent/workspace.py
  - id: openwiki-source-53e6f36a073f4241bf57b1e0
    resource: repo://core/app/routes/dev.py
  - id: openwiki-source-28674980a52d70f9fe3a7e3a
    resource: repo://tests/agent/test_checkpoint.py
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T18:35:21.080Z
---

# Checkpoints and undo

Undo in LUNA is a filesystem mechanism, not a framework feature. A **checkpoint** is a full copy
of a session's `workspace/` folder taken at a moment in time. Restoring one replaces the
workspace with that copy. Because the conversation log (`workspace/.runtime/messages.jsonl`)
lives inside the workspace, a restore rewinds the documents **and** the conversation together.

## The store

`CheckpointManager` (`core/agent/checkpoint.py`) works on two paths: the session's
`workspace/` and its `checkpoints/` folder. A session folder looks like this:

```
<session>/
├── workspace/        ← copied by each snapshot (including .runtime/)
├── checkpoints/
│   ├── c000_write/
│   ├── c001_edit/
│   └── c002_before_default-subagent_3fa2c1/
└── subagents/        ← not part of any snapshot
```

Rules the manager enforces or relies on:

- **The store lives outside the workspace.** The constructor refuses a `checkpoints_root` equal
  to or inside `workspace/`, since a snapshot would then copy itself.
- **Names are `c<NNN>_<label>`.** `NNN` is a zero-padded running number; the label is slugified
  (whitespace to `_`, anything outside word characters and `-` dropped; `\w` covers Cyrillic).
  Folders not matching the pattern are ignored by `list`.
- **Numbering comes from the listing.** The next number is `max(existing) + 1`, computed by
  listing `checkpoints/`. A counter file inside `.runtime/` would itself be rolled back by a
  restore and cause name collisions; the listing sits outside the workspace and never rewinds.
- **The manager holds no state.** It is constructed on demand wherever it is needed.
- **Subagent zones are not included.** They live beside `workspace/`, not inside it.

### Restore

`restore(checkpoint_id)` deletes `workspace/` and copies the snapshot back in its place. It is a
wholesale replacement, not a merge. It does not create a new checkpoint and does not delete
later ones — the "future" snapshots stay in the list. An unknown id raises `ValueError`.

The manager only touches files. Whoever calls it must also make the live agent re-read its
history, otherwise the agent would continue from an in-memory conversation that no longer
matches the restored files.

## When snapshots are taken

### After every successful agent write

Write tools are decorated with `@with_checkpoint()` *inside* `@agent_tool(...)`. The wrapper
awaits the tool and only then snapshots, using the tool's function name as label unless a string
or callable label is given. Consequences:

- a tool that raises (for example a `FileAccessError`) leaves **no** checkpoint;
- a snapshot is taken **after** the write, so checkpoint `cN` is the state the write produced;
- if the runtime context has no `checkpoints_root` (subagents), the decorator does nothing;
- if the snapshot itself fails, the error is logged and the tool still returns success. The
  write has already happened, and reporting failure would invite the model to retry a
  non-idempotent operation.

The manager is built from `get_runtime().context` on each call, so the same decorated tool
snapshots whichever session is running it.

### Before delegating to a subagent

`delegate_to_subagent` takes a snapshot labelled `before_<type>_<uid6>` **before** the subagent
starts. Undoing to that checkpoint returns the session to the state before the subagent's
advice was acted upon. It uses the same best-effort rule: no checkpoints root means skip, a
failure is only logged.

### After the user attaches or removes a file

Uploads over HTTP and `/attach` in the CLI do not go through a tool, so `@with_checkpoint`
never sees them. `WorkspaceManager` snapshots by hand after each write or delete (labels like
`attach report.pdf`) and returns the checkpoint id. Without that, the next undo would roll back
to a state before the file existed and silently drop it.

## Undo from the front ends

- **HTTP** — `GET /sessions/{id}/checkpoints` lists snapshots newest first.
  `POST /sessions/{id}/checkpoints/{cp_id}/restore` is refused with `409` while a turn runs,
  validates `cp_id` against the `c<NNN>_<label>` pattern and rejects separators and `..`,
  re-checks that the resolved folder is a direct child of the store, restores, and then calls
  the agent's `reload_history()`.
- **CLI** — `/cp` lists snapshots, `/undo <id>` restores and reloads the agent's working copy
  from its message store. `/reset` wipes the whole session folder instead.

## Cost

Every snapshot copies the entire workspace with `shutil.copytree`. On a large workspace with
frequent writes this becomes slow and uses disk quickly. Nothing prunes old checkpoints; they
disappear only when the session folder is deleted.

Related: [history and compaction](../architecture/history-and-compaction.md) for what the
restored log contains, [sessions and storage](sessions-and-storage.md) for the whole folder
layout.
