# 004. General-purpose subagent and a prompt refresh

- id: 004
- status: shaped
- source: own
- value: medium

## Why

The only subagent LUNA ships is `default-subagent`. Its prompt calls itself a throwaway stub for
testing the delegation mechanism, "to be replaced by domain specialists". The main prompt says
the same thing to the model. LUNA has no domain schema and will not grow domain specialists, so
the stub is in fact the one subagent the main agent has. The model is told not to rely on it.

The prompts around it have drifted too. The runtime reads `main_agent.ru.md`;
`main_agent.en.md` is a stale copy without the `<delegation>` section. Both still carry wording
from the time of the JSON document layer and the parent project. After the file tools landed
([001](001-file-tools-replace-json.md)) the prompts describe a tool set and a role that only
partly match the code.

## What changes

Rename `default-subagent` to `general-purpose`: the folder
`core/procedures/agents/general-purpose/`, the `name` in its front matter, and every mention in
prompts, docs and tests. No alias for the old name. An unknown type already returns a
self-correctable `DelegationError` that the model can act on.

Rewrite its `agent.md` as a real worker, not a stub. It takes a self-contained task: research
across the main agent's files (read-only through `workspace/`), a summary, a count, a draft. It
writes the deliverable to `artifacts/`, notes to `notes/`, and returns a short summary with the
artifact paths. The rules that make delegation safe stay: no writes to `workspace/`, no
retelling of whole files.

Refresh the main agent prompt as one pass over all of it, not only the delegation lines:

- `<delegation>` describes `general-purpose` and says when delegating is worth it (a large
  read-heavy subtask that would bloat the main context) and when it is not (a small edit the
  main agent can make itself).
- `<role>`, `<environment>` and `<tools>` match the current tool pool and workspace layout, with
  no leftovers from the JSON layer or the parent.
- `main_agent.en.md` is brought in line with `main_agent.ru.md`, section for section, so the
  English copy is usable again.

`docs/tools.md`, `docs/storage.md` and `docs/architecture.md` name the subagent by its new name.
The `text_editing` eval suite runs green after the change. A new eval scenario covers one
delegation round trip: the main agent hands a read-only task to `general-purpose`, reads the
artifact, and promotes it to `workspace/artifacts/`.

At startup `_materialize_procedures` copies `core/procedures/` over `<root>/procedures/` with
`dirs_exist_ok=True`. The copy adds and overwrites files but never removes them, so after the
rename a stale `agents/default-subagent/` stays in an existing `.session/` and the old type
still resolves. Materialization replaces the folder instead of merging into it.

## Why this is LUNA

LUNA is general-purpose by design: ordinary file tools over a working zone, no domain schema.
One general subagent fits that model. The delegation mechanism stays as it is; only its single
specialization stops pretending to be temporary.

## Out of scope

- New specialist subagents or a catalog of them.
- Choosing a subagent's tools or model per type in `agent.md`.
- Subagents that write into the main agent's workspace.
- A prompt language switch at runtime. The runtime keeps reading the Russian prompt.
