# OpenWiki instructions for LUNA

This wiki is LUNA's only project documentation. It replaced the hand-written `docs/` folder.

## Voice

- Write in English, as connected plain-language prose for a reader building a mental model of
  the system. Explain why a mechanism exists, not only what it does. Avoid spec dumps, symbol
  inventories and heavy abstractions; use tables only for genuinely tabular facts (routes,
  settings, commands).
- The source code and tests are authoritative. When a code comment, CLAUDE.md or another file
  disagrees with the code, describe the code.

## Scope

- Document the open-source project as it exists in this repository: the agent core, tools,
  storage model, interfaces, configuration, tests and development workflow.
- Do not describe internal business strategy, commercial plans, product tiers or anything from
  private notes. Content outside the repository's public code and docs is out of scope.
- `planning/` cards may be cited for the reasoning behind a design and for known drift, but the
  wiki describes current behaviour, not plans.
- `devfront/` (the web UI) is git-ignored while it is being reworked; mention it only where the
  server's behaviour depends on it.
