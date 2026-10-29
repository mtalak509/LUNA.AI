---
type: concept
title: Agent prompts (procedures)
description: Where LUNA's agent prompts live, how they are copied into a process folder and loaded with front matter stripped, the XML-section convention, how subagent types map to prompt folders, and the known drift between prompt copies.
tags: [prompts, procedures, agents, subagents, configuration]
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T17:46:46.267Z
sources:
  - id: openwiki-source-364a89a77ff1b7418dc1c6c3
    resource: repo://core/agent/base.py
  - id: openwiki-source-9cae2963ffbc49ec962d3395
    resource: repo://core/agent/bootstrap.py
  - id: openwiki-source-77582883137c489d92067600
    resource: repo://core/agent/tools/_subagents.py
  - id: openwiki-source-277976f7cb0a0aa61d455571
    resource: repo://core/agent/tools/delegation_tools.py
  - id: openwiki-source-8094eb2d8ff4d4977387259c
    resource: repo://core/procedures/agents/default-subagent/agent.md
  - id: openwiki-source-16fc89533f62b78bc2b4b9ac
    resource: repo://core/procedures/main_agent.en.md
  - id: openwiki-source-11ae840fe50156aa29722dff
    resource: repo://core/procedures/main_agent.ru.md
generated: { by: "claude-code", at: "2026-10-29T17:46:46.267Z" }
---

# Agent prompts (procedures)

In LUNA a "specialist" is a prompt, not a class. The main agent and every subagent run the same
`BaseAgent` code; what differs is the instruction file they are started with. Those files are
called **procedures** and live in the repository under `core/procedures/`:

```
core/procedures/
├── main_agent.ru.md                 the main agent's prompt — the one the runtime loads
├── main_agent.en.md                 an English copy (stale, see below)
└── agents/
    └── default-subagent/
        └── agent.md                 the only subagent type shipped today
```

## From repository to agent

1. **Materialize.** `init_process(root)` copies `core/procedures/` into `<root>/procedures/`
   (`~/.luna/sessions/procedures/` for the server, `~/.luna/cli/procedures/` for the CLI). The copy
   is refreshed on every process start, so editing a prompt in the repository takes effect after a
   restart, not live.
2. **Load.** `load_agent_prompt(procedures_root, rel_path)` reads the file and strips YAML front
   matter (`---` … `---`). The front matter carries machine fields (`name`, `description`) that
   are not sent to the model. The resulting text becomes `system_prompt` for `create_agent`.
3. **Pick the file.** The main agent always loads `main_agent.ru.md` (the constant
   `_MAIN_PROMPT_REL` in `core/agent/bootstrap.py`). A subagent of type `<type>` loads
   `agents/<type>/agent.md`.

Main agent and subagents load from the **same** materialized folder, through the same function.

## Subagent types are folders

There is no registry of subagent types. `delegate_to_subagent(subagent_type=...)` checks whether
`agents/<subagent_type>/agent.md` exists; if not, it returns a self-correctable
`DelegationError` ("unknown subagent type… available types are listed in your prompt"). Adding a
specialization therefore means adding a folder with an `agent.md` and listing it in the main
prompt's `<delegation>` section so the model knows it exists. See
[delegation](../workflows/delegation-and-subagents.md).

## Prompt structure

Prompts are written as XML-tagged sections, not Markdown headings. The main prompt has:

- `<role>` — the only conversation partner of the user, deciding every next step itself;
- `<environment>` — files in `workspace/`, notes in `notes/decisions.md` (append-only), the
  `<working_context>` block delivered before each step, attachments as a current listing that
  the agent must not write into, results in `artifacts/`;
- `<tools>` — each tool and the grep → windowed read → edit pattern, how to treat truncation
  notes, the two-step RAG pattern, how to write a self-contained `task_text`;
- `<delegation>` — the available subagent types and three separate decisions about a subagent's
  result: read the artifact, author any edit yourself against the current file, and promote
  files worth keeping into `workspace/artifacts/` (subagent zones are not checkpointed);
- `<working_method>`, `<constraints>`, `<response_style>` — read before editing, prefer `edit`
  over `write`, treat tool errors as feedback, read large files in windows, don't write in Plan
  mode, don't invent identifiers, answer briefly in Russian.

The working-context block injected by middleware uses the same XML-section format
(`<working_context>`, `<project_decisions>`, `<attachments>`), so the model sees one consistent
document (see [middleware](../architecture/middleware.md)).

The subagent prompt `default-subagent/agent.md` describes a cold context (only the task text),
its own `notes/` and `artifacts/`, read-only access to the main workspace through the
`workspace/` prefix, and asks for a short summary as the last message.

## Known drift

- **`main_agent.en.md` is not used** by the runtime and lacks the `<delegation>` section the
  Russian prompt has.
- **The only subagent describes itself as a stub.** `default-subagent` calls itself a technical
  placeholder for testing delegation, and the main prompt repeats that it "will be replaced by
  domain specialists". LUNA has no domain schema, so this is in practice the only helper the
  main agent has.

Idea card `planning/ideas/006-general-purpose-subagent.md` proposes renaming it to
`general-purpose`, rewriting it as a real worker, and refreshing both main prompts (see
[planning and direction](planning-and-direction.md)).

The Docker image additionally copies `core/procedures/` to `/procedures/`, but the runtime does
not read that copy; it materializes from the package as described above.
