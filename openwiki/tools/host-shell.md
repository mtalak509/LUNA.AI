---
type: tool reference
title: Host shell and session tasks
description: How LUNA runs Bash or PowerShell commands with host-user access, shares foreground and background tasks across agents, pages bounded output and cleans up owned processes when a session closes.
tags: [shell, tools, processes, sessions, lifecycle, output]
verified:
  - by: openwiki/0.7.2
    at: 2026-11-09T20:38:09.193Z
sources:
  - id: openwiki-source-a87578127072d9fd00cf3737
    resource: repo://cli/app.py
  - id: openwiki-source-0999e357957975c8879ee999
    resource: repo://cli/commands.py
  - id: openwiki-source-9cae2963ffbc49ec962d3395
    resource: repo://core/agent/bootstrap.py
  - id: openwiki-source-d23fb6387169d3d01a2355f0
    resource: repo://core/agent/middleware/decision.py
  - id: openwiki-source-c1be0e08be41c4473bf84742
    resource: repo://core/agent/middleware/permission.py
  - id: openwiki-source-07e76361ddee296c51381ff5
    resource: repo://core/agent/session.py
  - id: openwiki-source-72e508b5f32e77d8c194baa9
    resource: repo://core/agent/shell/logs.py
  - id: openwiki-source-ef44557fa5de39f25a459d70
    resource: repo://core/agent/shell/models.py
  - id: openwiki-source-b3b30b817c9c2ba06d7f090a
    resource: repo://core/agent/shell/posix_process.py
  - id: openwiki-source-4c191222105a52799537e04a
    resource: repo://core/agent/shell/shell_manager.py
  - id: openwiki-source-beab910b4b7051941a357fa2
    resource: repo://core/agent/shell/win_job.py
  - id: openwiki-source-b7a3fbd681395fd1518b26c1
    resource: repo://core/agent/shell/windows_process.py
  - id: openwiki-source-277976f7cb0a0aa61d455571
    resource: repo://core/agent/tools/delegation_tools.py
  - id: openwiki-source-6e479670aac673f34064e1c5
    resource: repo://core/agent/tools/shell_tools.py
  - id: openwiki-source-ab5a4aaa5b635da70e735b12
    resource: repo://core/agent/tools/tool_output.py
  - id: openwiki-source-005cbaacecc681a00b4b1e60
    resource: repo://core/app/routes/_deps.py
  - id: openwiki-source-23777f9174e92ec97c2941a7
    resource: repo://core/app/routes/shell.py
  - id: openwiki-source-3a88e4d15da7c9e7b69e7b29
    resource: repo://core/app/server.py
generated: { by: "codex", at: "2026-11-09T20:38:09.193Z" }
---

# Host shell and session tasks

The `shell` tool lets main agents and subagents run commands on the machine running LUNA.
It uses Bash on POSIX and PowerShell on Windows, with the filesystem and network permissions
of the user running the service. In Docker that machine is the container. There is no command
allowlist, network filter or PathScope restriction inside the command.

One `ShellManager` belongs to each live session. The main agent and every delegated subagent
receive the same object, so a task started by a helper remains available to main after the
helper returns. Different sessions have different task registries. The manager owns processes
and their logs, rather than delegating ownership to the coroutine that happens to call it.

## Actions and arguments

All actions use the same tool and return a JSON string. The argument model forbids unknown
fields and rejects fields that do not belong to the selected action, even when supplied with
a default value.

| Action | Required | Optional | Result |
|---|---|---|---|
| `run` | nonblank `command` | `cwd`, `background=false`, `timeout` | new task metadata; foreground also returns the first output page |
| `status` | nonblank `task_id` | none | task metadata |
| `output` | nonblank `task_id` | `offset=0` | metadata plus a bounded output page |
| `stop` | nonblank `task_id` | none | metadata after owned-process cleanup |

`timeout` must be a finite positive number, `background` a boolean and `offset` a nonnegative
integer; booleans are not offsets. `run` refuses `task_id` and `offset`. Management actions
refuse execution options such as `cwd`, `command` and `background`; only `output` accepts
`offset`. A missing manager or unknown task raises a `ShellError` surfaced through the normal
tool-error middleware.

The default cwd is `workspace/` for main and the caller's own `subagents/<uid>/` for a helper.
Relative cwd is resolved against that directory; absolute cwd may be outside the session.
It must resolve to an existing directory. These are native host paths, not file-tool prefixes:
main's cwd is already the workspace, and a helper's `workspace/` prefix has no special meaning
to the shell.

Each run starts an independent shell with inherited environment, closed stdin and combined
stdout/stderr. A `cd`, export or environment activation affects that command and its children
only. POSIX launches `bash --noprofile --norc -c`; Windows prefers `pwsh`, then `powershell`,
using a non-interactive, profile-free encoded command with UTF-8 output. This is not an
interactive terminal: there is no PTY or stdin-writing action.

## Foreground and background work

A foreground call waits for completion and defaults to a 120-second timeout. A background
call (`background=true`) returns after startup while the command is running, unless it already
finished or failed. Background tasks have no default deadline; either mode may set an explicit
timeout in seconds. Startup work counts toward the execution deadline after spawning begins.

Use the background flag for a server or worker that must stay alive across later tool calls.
Shell-level detachment is not the ownership contract: when the initial shell exits normally,
the manager also terminates its owned descendants, even if a descendant still holds the output
pipe open. Ordinary POSIX descendants are tracked by process group; deliberately escaping that
group is outside this cleanup guarantee.

At most **eight tasks may run concurrently per session**, shared across agents. Admission and
slot reservation happen without yielding on the session event loop. A terminal task releases
its slot but stays in the registry for status and output reads. There is no persisted task
metadata or task recovery after a process restart.

## Reading results correctly

Metadata includes `task_id` (`sh_<uuid hex>`), command, resolved cwd, creator namespace,
creation/finish timestamps, status, exit code, reason and diagnostic error. Task snapshots are
immutable. Optional output fields are null on metadata-only responses.

| Status | Meaning |
|---|---|
| `running` | execution or required cleanup is still active |
| `completed` | the shell exited and owned-process/output cleanup finished; inspect `exit_code` |
| `stopped` | explicitly stopped, cancelled as foreground work, closed with the session, or stopped after an output/capture error |
| `timed_out` | the execution deadline requested termination |
| `failed` | startup failed before a process was owned; reason is `launch_error` |

**A nonzero exit code is still `completed`**, not `failed`. Reasons distinguish `user_stop`,
`turn_cancelled`, `session_closed`, `output_limit` and `launch_error`; a timeout or capture
failure may have no reason. Read `error` for diagnostics. Status and captured output are
snapshots; a running command can change between calls.

For example, on POSIX an agent can make these calls in sequence:

```json
{"action":"run","command":"python worker.py","background":true}
```

Keep the returned `task_id`, then substitute it in management calls:

```json
{"action":"status","task_id":"sh_<returned id>"}
{"action":"output","task_id":"sh_<returned id>","offset":0}
{"action":"stop","task_id":"sh_<returned id>"}
```

The command must fit the host: use the installed Python executable and shell syntax for that
OS. The task registry does not emit completion notifications; inspect status when needed.

## Output pages and disk limits

Combined stdout/stderr is captured as bytes in `<session>/shell_tasks/<task_id>.log` outside
the workspace. Each log is capped at **50 MiB**. Filling it exactly is allowed; receiving
additional bytes requests `stopped` with reason `output_limit` and cleans up the process tree.
Logs are retained until the session's files are removed; they are not checkpointed.

An output page contains at most **2,000 lines or 50 KiB of rendered UTF-8 text**, whichever is
reached first. Task metadata remains separate from that limit. `offset` and `next_offset`
count bytes in the original log, not characters or lines. Always use the returned continuation
offset rather than computing one from the decoded string.

- `has_more=true` means more captured bytes remain after the page; request `next_offset`.
- `eof=true` means the read reached the currently captured end. It can be true while status
  is still `running`; it does not announce process completion.
- An empty running page may simply mean no output has arrived. Inspect status and read again
  when needed.

The reader preserves valid UTF-8 boundaries, replaces invalid bytes and retains an incomplete
character at the end of a running log for a later read. An explicit offset inside a valid
character, past the captured end or below zero is rejected. A very long line can be continued
by byte offset without dropping its remainder. This differs from the line-based `read` tool;
see [file tools](file-tools.md).

## Modes, user controls and undo

`shell` is one `is_write=True` tool with no checkpoint decorator. **Plan hides and blocks all
four actions**, including inspection and stopping. `execute_shell` also checks Act before
dispatch. **Confirm requests approval for every invocation**, including `status`, `output`
and `stop`; Accept-all proceeds without the gate. A subagent inherits both modes from the
parent turn, including Confirm for its file writes.

Users can control existing tasks outside agent turns and middleware:

| Surface | List | Stop |
|---|---|---|
| CLI | `/shell tasks` | `/shell stop <task_id>` |
| HTTP | `GET /sessions/{id}/shell/tasks` | `POST /sessions/{id}/shell/tasks/{task_id}/stop` |

These controls work in Plan; HTTP also allows them while an agent turn is busy. They cannot
launch a command. Stop on a terminal task is idempotent. Unknown HTTP sessions/tasks give
404; a session without a manager gives 503. See [CLI](../interfaces/cli.md) and
[HTTP API](../interfaces/http-api.md).

Shell creates no automatic checkpoints, and undo does not revert host side effects, task state
or logs. Background work can keep modifying files while no agent turn is active. Stop relevant
tasks before restoring files they use; see [checkpoints](../concepts/checkpoints-and-undo.md).

## Ownership and shutdown

Cancelling an awaited foreground call stops its task with `turn_cancelled`. Background tasks
survive caller cancellation, an answer, subagent completion and a switch to Plan. They stop on
explicit task stop or session closure: CLI exit/reset, HTTP session deletion and server lifespan
shutdown all await session cleanup.

POSIX launches each task in a new session/process group, sends SIGTERM to the group and allows
two seconds before SIGKILL. Windows starts a gated Python bootstrap, assigns it to a Job before
releasing command execution, and closes that Job to stop owned processes. Both capture output
until cleanup finishes before publishing terminal state.

Cleanup is shielded against caller cancellation. If OS cleanup fails, the task retains its
running slot and records an error; a later stop retries cleanup rather than declaring a process
gone. Session deletion/reset waits for successful close before removing or replacing state.
Registry shutdown attempts all sessions and can be retried. These are orderly-close guarantees,
not restart recovery or a promise of cleanup after an uncatchable process crash.

Tests in `tests/agent/test_shell_*.py` cover contracts, processes, logs, modes and lifecycle;
`test_shell_integration.py` checks a helper-started server controlled by main. The live
`tests/evals/shell/` scenario checks explicit background/status/output/stop behavior. See
[tests and live evals](../testing/tests-and-live-evals.md) and
[sessions and storage](../concepts/sessions-and-storage.md).
