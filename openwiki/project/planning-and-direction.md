---
type: project process
title: Planning, digests and design rules
description: Where LUNA's decisions and documentation live — idea cards in planning/, parent-release digests used as scouting material, the changelog, local specs, this wiki — and the standing design rules new work must respect.
tags: [planning, decisions, roadmap, governance, documentation]
sources:
  - id: openwiki-source-a2371d6362e5db4bc834ad03
    resource: repo://CLAUDE.md
  - id: openwiki-source-9be528868079f7d782a23170
    resource: repo://planning/ideas/006-general-purpose-subagent.md
  - id: openwiki-source-1e08ee0705b8be367ac2f608
    resource: repo://planning/index.md
  - id: openwiki-source-a01ee747afcc71ec1338db0e
    resource: repo://planning/README.md
generated: { by: "claude-code", at: "2026-10-29T18:35:21.080Z" }
verified:
  - by: openwiki/0.7.2
    at: 2026-10-29T18:35:21.080Z
---

# Planning, digests and design rules

The code says how LUNA works today. Why it is built that way, and what gets built next, lives in a
few other places in the repository. This page explains each of them and how they relate.

| Place | What it is | What it is not |
|---|---|---|
| `openwiki/` (this wiki) | how LUNA works today, kept in sync with the code | a plan |
| `planning/` | the decision log: one card per idea, with status | a description of current behaviour |
| `digests/<version>/digest.md` | summaries of releases of the parent project | a backlog |
| `CHANGELOG.md` | what shipped, per release | a plan |
| `specs/<feature>/` | per-feature specs and plans (local, git-ignored) | shared history |

## Origin and independence

LUNA started as a fork of `analyzer-agent-service` and reused its agent core, but it is now an
independent open-source project. Decisions are **not** carried over from the parent by default.
Some code comments still use the parent's vocabulary (references to "V2", principle labels like
"П2", plan section numbers like "15.1"); these are historical markers, not live documents.

## Idea cards (`planning/`)

`planning/README.md` defines the format. One idea is one file `planning/ideas/NNN-short-name.md`;
the number is permanent and the file is never renamed. Cards are in English and have the
sections *Why*, *What changes*, *Why this is LUNA* and *Out of scope*, plus `id`, `status`,
`source` (`own` or a digest path) and `value`.

Statuses: `seed` (a note) → `shaped` (why, boundary and LUNA check written) → `accepted` →
`building` → `done` (detail moves to the changelog), or `parked` / `dropped` (a dropped card stays
so the question is not reopened). `planning/index.md` is the table of all cards and is updated in
the same change as a card.

At the time of writing the index lists:

| ID | Idea | Status |
|---|---|---|
| 001 | File tools replace the JSON tool layer | done |
| 002 | Truncate tool output with a decorator | building |
| 003 | Host shell tool | accepted |
| 004 | CLI on prompt_toolkit and rich | building |
| 005 | Agent Client Protocol adapter for Zed | shaped |
| 006 | General-purpose subagent and a prompt refresh | shaped |
| 007 | Run the CLI from any directory | seed |

A card earns its place when it makes LUNA easier to understand, extend or run locally. Card 006
is a good example of the kind of drift cards record: the only shipped subagent is still a
self-described stub, and the English main prompt is a stale copy (see
[prompts and procedures](prompts-and-procedures.md)).

## Parent-release digests (`digests/`)

Digests of parent releases land in `digests/<version>/digest.md`. They describe what the parent
shipped and how. They are **scouting material**: each item is weighed against LUNA's own
architecture (filesystem-first, no database, no domain schema) before anything is proposed, and an
idea that survives becomes a card whose `source` points at the digest. The card must stand on its
own without the digest.

## Documentation

This wiki replaced the hand-written `docs/` folder. It is regenerated from the code rather than
edited by hand; how to update it is described in
[development and deployment](../operations/development-and-deployment.md).

## Standing design rules

These are the constraints new work is checked against. Each is visible in the code:

- **No database, no orchestrator process.** The agent server is the whole backend; session state
  is materialized on the filesystem.
- **No LangGraph checkpointer.** The graph is stateless between turns; LUNA owns the message
  list and its log (see [history and compaction](../architecture/history-and-compaction.md)).
- **No orchestration graph over the agent.** The agent chooses its own route; delegation is a
  tool call (see [architecture overview](../architecture/overview.md)).
- **One flat tool pool, specialization by prompt.** No per-agent tool registries (see
  [tool pool](../tools/tool-pool.md)).
- **General-purpose file tools, no domain schema.** The agent works with the user's files by
  path; there is no privileged document format.
- **Every behavioural feature ships with live scenarios** next to its unit tests (see
  [tests and evals](../testing/tests-and-live-evals.md)).
