# 009. Align the prompt with the tool set: append and artifact promotion

- id: 009
- status: shaped
- source: own
- value: high

## Why

The main agent prompt asks for two things the tools cannot do cleanly.

- **Append-only notes.** `notes/decisions.md` is described as append-only, but there is no
  append. The agent either rewrites the whole file with `write`, which can drop earlier
  entries, or uses `edit` with an anchor line, which breaks when the anchor changes or
  repeats.
- **Promoting subagent artifacts.** The delegation section says to move a file from the
  subagent zone into `workspace/artifacts/` "via `write`". That means read, then write by
  hand. It works for text and cannot work for a binary file.

LUNA itself pointed at both during a self-review of its tools in the CLI.

## What changes

For each contradiction, either the tools gain the operation or the prompt stops asking for
it. To decide:

- **Append.** Options: a `mode="append"` flag on `write`, a separate `append` tool, or a
  prompt that drops "append-only" and says "edit by adding, never remove entries".
- **Promotion.** Options: a binary-safe `cp(src, dst)` (and maybe `mv`) that respects
  `PathScope` (read from the subagent zone, write to `workspace/`), or a prompt that limits
  promotion to text files.

Whichever way, the prompt and the tool descriptions say the same thing.

## Why this is LUNA

State lives in files, and the agent moves results between zones. Both operations are plain
file operations and need no new storage.

## Out of scope

- Reading binary formats (card [012](012-non-text-documents.md)).
