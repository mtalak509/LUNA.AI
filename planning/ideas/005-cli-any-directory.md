# 005. Run the CLI from any directory

- id: 005
- status: seed
- source: own
- value: high

## Why

The CLI always works in one fixed place: `~/.luna/cli/dev/`. There is a single session with
a hardcoded id, and the folder you start `luna` from does not matter. To work on files you
first copy them into the zone with `/attach`. You cannot keep separate sessions for separate
projects, and you cannot point the agent at a folder you already have.

The goal is to start `luna` inside any folder and have the agent work there, the way a
developer uses a coding agent inside a repository.

## What changes

Not decided yet. Today the CLI paths are fixed in `cli/config.py` (`SESSION_ROOT`,
`SESSION_ID`, `SESSION_DIR`). They sit under `LUNA_HOME` in `core/config.py`, which is
deliberately not configurable. `.env` is read from the repository root, not from the
current directory.

Open questions:

- **Where the zone is.** Either the current directory becomes the working zone itself, or it
  only selects a session, and the zone stays under `~/.luna/`.
- **Where session state lives.** History, checkpoints, and subagent folders could go in a
  `.luna/` folder inside the project, or in `~/.luna/` under a key derived from the project
  path.
- **Checkpoints and undo.** Snapshots copy the whole zone. A large real folder makes every
  write expensive. A snapshot may need to skip some paths, or work in a different way.
- **What the agent may touch.** `PathScope` limits file tools to the zone. If the zone is a
  real project, it is the boundary of that project.
- **Several sessions per folder.** Decide whether to resume the last one, start a new one,
  or pick one with a flag.
- **Config lookup.** `.env` is in the repository today. An installed `luna` needs a user-level
  config, and possibly a per-project override.
- **Spilled tool output.** `.truncated_outputs/` (card
  [002](002-truncate-tool-output.md)) sits in the zone today. If the zone becomes a real
  project folder, it would clutter that project, so it should move with session state. The
  location comes from `PathScope.spill_dir` alone; if it leaves the zone, the agent needs read
  access to it by an explicit prefix, the way `subagents/` works now.
- **The web server.** Decide whether it gets the same option or stays on
  `~/.luna/sessions/`.

## Why this is LUNA

LUNA is filesystem-first. The working zone is a folder, and session state is files. Letting
that folder be one the user already has fits the model without adding a database or a
server process.

## Out of scope

- Changing how web sessions are stored, unless the decision above says otherwise.
