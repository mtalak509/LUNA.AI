# 003. Host shell tool

- id: 003
- status: accepted
- source: own, digests/1.2.0
- value: high

## Why

File tools cannot run the commands a developer already runs on the machine: tests, git,
formatters, scripts. The parent added `bash` in 1.2.0 inside a per-session Linux user,
nftables, and `NET_ADMIN`. That cage is the parent's deployment, not LUNA's.

LUNA runs as the user who started it. On a developer machine that user is the developer.
In a container or VM the container is the boundary. The same tool covers both. A second,
locked-down shell does not belong in the core.

## What changes

One shell tool. It runs the host shell: PowerShell on Windows, bash on Linux and in a
container. Any command, pipes, network, and child processes are allowed, as that user.
There is no command allowlist and no nftables inside the process.

The working directory is the agent's zone. `PathScope` does not apply inside the child
process: the shell can leave the zone, read `.runtime`, and write outside the session.
That is accepted. The tool does not pretend to be a jail.

Before the command, snapshot the working zone the same way `write` and `edit` do. Undo
still restores only that zone, not the rest of the disk. In confirm mode the user sees
the command string. Output goes through the truncation decorator in
[002](002-truncate-tool-output.md).

A subagent does not get this tool. Otherwise the rule that a subagent does not change the
main agent's documents cannot be enforced.

## Why this is LUNA

The core stays one program a single engineer can run locally. Isolation of a shared
deployment is the machine or container around that program, not a second implementation
of the tool.

## Out of scope

- The parent's per-session uid, nftables, and `NET_ADMIN`.
- A separate shell for a multi-user deployment.
- Two tools, `bash` and `powershell`, with different contracts.
- Teaching the shell to honor `PathScope` or to hide `.runtime`.
