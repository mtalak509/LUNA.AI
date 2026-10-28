# 010. Fewer round trips in grep, edit and ls

- id: 010
- status: seed
- source: own
- value: medium

## Why

Some answers from the file tools are too thin, so the agent makes an extra call after them:

- `grep` returns only the matching line. Understanding a hit almost always takes a `read`
  with an offset. There is also no way to list matching files only, or to cap the number of
  hits.
- `edit` returns only `замен: N`. To see what changed, the agent re-reads the file.
- `ls` returns names only. "Which file is newest?" cannot be answered without reading
  everything.

## What changes

Candidates, each small and independent:

- `grep`: `context` lines around a hit, `files_only`, `max_results`.
- `edit`: return the changed fragment or a short diff.
- `ls`: an opt-in `details` flag with size and modification time.

Each one is weighed against the extra tokens it adds to the output. Output truncation
(card [002](002-truncate-tool-output.md)) already caps the worst case.

## Why this is LUNA

The tools stay general-purpose. The change makes common chains (`grep` → `read` → `edit`)
shorter.

## Out of scope

- An undo tool over checkpoints. Rewinding is a user action today and stays one.
- File inputs for `delegate_to_subagent`.
