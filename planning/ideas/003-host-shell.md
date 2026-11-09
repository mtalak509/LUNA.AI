# 003. Host shell tool

- id: 003
- status: building
- source: own, digests/1.2.0
- value: high

## Why

File tools cannot run the commands a developer already runs on the machine: tests, git,
formatters, scripts. LUNA needs a shell for that work.

LUNA runs as the user who started it. On a developer machine that user is the developer.
In a container or VM the container is the boundary. The same tool covers both. A second,
locked-down shell does not belong in the core.

## What changes

One shell tool. It runs the host shell: PowerShell on Windows, bash on Linux and in a
container. Any command, pipes, network, and child processes are allowed, as that user.
There is no command allowlist and no network filter inside the process.

The working directory is the agent's zone. `PathScope` does not apply inside the child
process: the shell can leave the zone, read `.runtime`, and write outside the session.
That is accepted. The tool does not pretend to be a jail.

The same tool is available to main and subagents in Act. Its actions are
`run | status | output | stop`. Confirm asks before every invocation through the existing
decision middleware; Accept-all does not ask. Plan hides and blocks the entire tool.
Subagents inherit the parent's decision mode for all tools.

No snapshots are created for shell; checkpoint undo cannot undo its effects. Each invocation
starts a fresh noninteractive shell with closed stdin and inherited environment. Foreground
runs wait with a 120-second default timeout; explicit background runs return a task ID and have
no default deadline. An explicit positive timeout stops the owned process tree.

One manager per session owns tasks, merged stdout/stderr disk logs and process cleanup.
Agents share task IDs and can poll status/output or stop a task. Output pages are limited to
2,000 lines / 50 KiB with byte-offset continuation; each task has a 50 MiB log cap that stops
it on overflow. At most eight tasks run concurrently. Normal shell exit also cleans up owned
descendants; use the background flag rather than shell detachment.

Tasks survive answers, subagent completion and Plan transitions. Closing/resetting/deleting the
session or shutting down stops them. There is no restart recovery guarantee or completion event
broker. Users can list/stop tasks directly through CLI and HTTP, including in Plan:
`/shell tasks`, `/shell stop <task_id>`, `GET /sessions/{id}/shell/tasks`,
`POST /sessions/{id}/shell/tasks/{task_id}/stop`.

## Why this is LUNA

The core stays one program a single engineer can run locally. Isolation of a shared
deployment is the machine or container around that program, not a second implementation
of the tool.

## Out of scope

- A per-session user, a host firewall, or any other jail inside the LUNA process.
- A separate shell for a multi-user deployment.
- Two tools, `bash` and `powershell`, with different contracts.
- Teaching the shell to honor `PathScope` or to hide `.runtime`.
