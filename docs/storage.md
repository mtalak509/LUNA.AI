# Storage and sessions

LUNA keeps no database. Everything a session knows lives as files on disk, and the layout of
those files *is* the state. This document shows where things go and why.

## The home folder

All of LUNA's on-disk state lives under one fixed home directory: **`~/.luna/`** (on Windows,
`%USERPROFILE%\.luna`). This is deliberately fixed and not configurable — like Claude Code's
`~/.claude`. Python's `Path.home()` resolves it per OS, so there's no platform-specific code.

The two front ends keep their state in separate subfolders so they never clobber each other:

```
~/.luna/
├── sessions/     ← the HTTP server's sessions (one subfolder per session)
└── cli/          ← the CLI's single fixed session
```

## A session folder

Whether created by the server or the CLI, a session has the same shape. Here it is:

```
<session>/
├── workspace/                 the main agent's read-write zone
│   ├── <your documents>       e.g. report.json, data/notes.md — whatever you work on
│   ├── notes/
│   │   └── decisions.md       running notes; injected into the agent's context each turn
│   ├── artifacts/             results worth keeping
│   └── .runtime/
│       └── messages.jsonl     the append-only chat history (hidden from the agent's file tools)
│
├── checkpoints/               snapshots of workspace/ (the undo history)
│   ├── c000_write_file/
│   ├── c001_json_patch/
│   └── ...
│
└── subagents/                 one folder per delegated subagent
    └── <uid>/
        ├── notes/             the subagent's scratchpad
        ├── artifacts/         the files it produced (its deliverables)
        └── .runtime/
```

The whole session is self-contained under this one folder, so deleting a session is just deleting
the folder.

## The three zones and who can touch them

LUNA does not isolate agents in separate processes. Instead it isolates them by **controlling
which paths each one can reach**. Every file tool resolves its path through a small gatekeeper
(`PathScope`, [core/agent/tools/fs_paths.py](../core/agent/tools/fs_paths.py)) that enforces these
rules:

| Zone | Main agent | Subagent |
|---|---|---|
| `workspace/` | read + write | **read only** |
| its own `subagents/<uid>/` | read + write (to collect results) | read + write |
| `.runtime/` | no access | no access |

So the main agent owns the working documents; a subagent can *look at* them but only *writes* into
its own folder. That's the mechanism behind "a subagent is an advisor, not an editor" from
[architecture.md](architecture.md).

Two safety rules are baked into the gatekeeper regardless of agent:

- **You can't escape the zone.** Relative paths only; `..` tricks and symlinks that would climb
  out are rejected.
- **`.runtime/` is off-limits to file tools.** The history file lives there, and letting the agent
  read or overwrite its own message log would be a foot-gun. It's hidden from listings too.

## The chat history file

`workspace/.runtime/messages.jsonl` is the durable record of the conversation — one JSON message
per line, append-only. The agent's in-memory message list is a working copy of this file. Keeping
it on disk (rather than only in memory) is what lets a session survive being rebuilt and lets undo
rewind the conversation. The model's private reasoning tokens are stripped before writing, so they
never land here (see [runtime.md](runtime.md)).

## Checkpoints (undo)

A checkpoint is simply a **full copy of `workspace/`** at a moment in time, saved under
`checkpoints/` with a running number and a label, like `c003_json_patch`. The code is
`CheckpointManager` ([core/agent/checkpoint.py](../core/agent/checkpoint.py)).

When they're taken:

- **After every successful write tool.** A `@with_checkpoint` decorator wraps each write tool and
  snapshots once the write succeeds. If the tool fails, no checkpoint is made.
- **Before delegating to a subagent** — so you can roll back to the state before the subagent
  touched anything.

A few things worth knowing:

- The snapshot **includes `.runtime/`**, so restoring rewinds the documents and the chat together.
- Subagent folders are **not** included — they live beside `workspace/`, not inside it.
- The checkpoint store sits **outside** `workspace/`, so a snapshot never copies itself.
- Restoring replaces `workspace/` wholesale with the chosen snapshot (it's not a merge), then the
  agent reloads its history from the restored file.
- The checkpoint number is derived by listing the store, not from a counter file — a counter would
  itself get rolled back and cause collisions.

Because each checkpoint copies the entire workspace, frequent writes on a large workspace get slow.
That's a known issue, tracked as TD-15 in [tech-debt.md](tech-debt.md).

## Procedures — the agent's instructions

The agents' instruction files (prompts) are not part of a session. They live in the repo under
`core/procedures/` and are copied into a process-level `procedures/` folder once at startup:

```
procedures/
├── main_agent.ru.md              the main agent's instructions (the one the runtime loads)
├── main_agent.en.md              an English copy of the same
└── agents/
    └── <subagent-type>/
        └── agent.md              a subagent specialization's instructions
```

This copy is refreshed on every startup, so editing a prompt in the repo takes effect on restart.
To add a new subagent specialization, you add a folder here with an `agent.md`; the main agent
learns which types exist from its own prompt.

## Session lifecycle

**Start.** The server mints a session id, creates the folder, and builds a main agent for it. The
CLI does the same for its single fixed session (`~/.luna/cli/dev/`).

**Work.** Each of your messages runs a turn; each successful write leaves a checkpoint behind.

**Pause.** If the agent needs to ask you something, the turn parks on that question and waits for
your answer (see [modes-and-hitl.md](modes-and-hitl.md)).

**End.** Deleting a session cancels any running turn and removes its folder. There is currently no
automatic timeout — abandoned sessions live until the process restarts or you delete them (tracked
as TD-13 in [tech-debt.md](tech-debt.md)). On server startup, leftover session folders from a
previous run are wiped, since the in-memory registry didn't survive the restart anyway.

## Next

- What the file and JSON tools do with these zones: [tools.md](tools.md)
- How history and checkpoints are used during a turn: [runtime.md](runtime.md)
