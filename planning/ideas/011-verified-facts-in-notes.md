# 011. Only verified facts go into decisions.md

- id: 011
- status: seed
- source: own
- value: high

## Why

`notes/decisions.md` is injected into every step through `<project_decisions>`. Whatever is
written there steers the agent for the rest of the session.

In a CLI self-review, the agent (DeepSeek v4.1 flash on OpenRouter) took one `find("**/*")`
call made while the zone really was empty, and turned it into a bug report: "`find` misses
existing files". It never re-ran that call once files existed. Re-running it shows `find`
works. The agent then wrote this as a **Fact** into `decisions.md`, together with a made-up
admission that it had earlier misreported the zone as empty. From then on, every step
carried a false fact.

## What changes

A prompt rule, roughly: write something to `decisions.md` as a fact only if a tool call in
the current step confirms it, and name that call. Guesses and conclusions are labelled as
such.

To check: an e2e scenario where the start state invites a wrong generalisation, and the
judge looks at what ended up in `decisions.md`.

## Why this is LUNA

Filesystem-first memory is only as good as what gets written to it. The rule costs nothing
at runtime.

## Out of scope

- Automatic validation of notes by code.
