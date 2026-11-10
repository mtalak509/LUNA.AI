# 014. Sandboxed shell via bubblewrap

- id: 014
- status: seed
- source: own
- value: high

## Why

Card 003 gave the shell full host-user rights and stated the tradeoff plainly: `PathScope` does
not apply inside the child process, so the shell can leave the zone, read `.runtime`, and write
anywhere the user can. That was accepted because a second, locked-down shell did not belong in
the core.

The cost of that decision is now visible. File tools are jailed by `PathScope`; the shell is not.
A user who reads "LUNA edits files in my workspace" reasonably assumes one trust boundary, while
the system actually has two. The gap is not theoretical: a document can carry injected
instructions, and the shell will execute them with the user's full rights — including network
access, `~/.ssh`, and every other repository on the machine.

This card asks whether the shell should run inside a bubblewrap sandbox instead. It deliberately
reopens the "no jail inside the LUNA process" line of card 003.

## What changes

`spawn_posix_shell` stops calling `bash` directly and calls `bwrap` instead. The sandbox is
described as a mount policy, not as a command filter:

- system paths (`/usr`, `/lib`, `/lib64`, `/bin`, `/etc`) mounted read-only;
- the agent's zone mounted read-write at the same path;
- `--proc /proc`, `--dev /dev`, `--tmpfs /tmp`;
- `--unshare-net` for the strict mode, omitted for the network mode;
- `--die-with-parent` so a killed LUNA does not leave orphans;
- `--new-session` to detach from the controlling terminal.

Three modes, mirroring the shape Codex already ships (`read-only`, `workspace-write`,
`danger-full-access`):

| Mode | Zone | Network | Use |
|---|---|---|---|
| `read-only` | read-only | no | inspection, greps, `git log` |
| `workspace-write` | read-write | no | default for document work |
| `full-access` | read-write | yes | builds, `git push`, `pip install` |

The mode is a session setting, orthogonal to Plan/Act. Plan still hides the tool entirely;
Confirm still asks before every invocation. The sandbox narrows what an approved command can
reach — it does not replace the approval gate.

If `bwrap` is missing or unusable, the tool falls back to today's unsandboxed behaviour and says
so in the result, rather than failing the turn.

## Advantages

- **Closes the prompt-injection path.** An injected `curl … | bash` has no network; an injected
  `rm -rf ~/Documents` has no such directory. The block comes from the kernel, not from the
  model's judgement.
- **One trust boundary instead of two.** The shell and the file tools finally agree on what the
  agent can touch.
- **No root, no daemon, no container.** `bwrap` is an ordinary unprivileged binary. Verified on
  this host: `bwrap 0.9.0` at `/usr/bin/bwrap`, `kernel.unprivileged_userns_clone = 1`, and a
  sandboxed `sh` ran as uid 1000 with `/home/maxim` absent and `/usr` read-only.
- **Cheap to adopt.** One spawn site (`core/agent/shell/posix_process.py`), one policy builder,
  one mode field. The task manager, logs, pagination and timeouts are untouched.
- **Proven in the field.** Flatpak runs every application this way. Codex bundles its own
  `bubblewrap` (56 string references, plus `landlock` 38 and `seccomp` 25); Claude Code requires
  it for subprocess isolation and calls disabling it "loses subprocess isolation".
- **Reversible per call.** Unlike Landlock, the policy lives only as long as the process. A
  wrong mount list is a bug, not a permanent restriction.
- **Process isolation for free.** The sandboxed process sees only itself, so it cannot signal or
  inspect unrelated host processes.

## Drawbacks

- **Breaks ordinary commands.** `git push` needs network and `~/.ssh`; `pip install` writes to
  `~/.cache`; `docker` needs the socket; `npm` needs `~/.npm`. Every one of these becomes a mount
  entry that someone must remember to add. A missing mount surfaces as "No such file or
  directory", not as "blocked by policy" — the same trap that made `awk` disappear in a test
  because `/etc` was not mounted.
- **Ubuntu 24.04 restricts unprivileged user namespaces.** This host has
  `kernel.apparmor_restrict_unprivileged_userns = 1`. `bwrap` 0.9.0 ships an AppArmor profile
  that is expected to cover it, but this must be verified on a clean install rather than assumed.
- **Linux only.** macOS would need Seatbelt, Windows a separate mechanism. A cross-platform
  contract means three implementations or a documented Linux-only guarantee.
- **Nested sandboxes.** Running LUNA inside Docker, or running `docker` from inside the sandbox,
  needs care: user namespaces do not nest freely, and the Docker socket is a privilege
  escalation by itself.
- **Debugging gets harder.** "Permission denied" and "file not found" now have a third possible
  cause. Users will blame LUNA for a policy they cannot see.
- **Not a resource limit.** CPU, memory and disk are cgroups' job. A fork bomb inside the
  sandbox is still a fork bomb.
- **Network is all-or-nothing.** `--unshare-net` is binary. Per-host filtering needs a proxy —
  the approach Claude Code takes with a TLS-terminating proxy and its own CA. That is a much
  larger piece of work and does not belong in this card.
- **Contradicts a recorded decision.** Card 003 lists "any other jail inside the LUNA process" as
  out of scope. Adopting this card means amending 003, not quietly overriding it.

## Open questions

- Does `bwrap` work out of the box on a stock Ubuntu 24.04 with the AppArmor userns restriction?
- Which mounts are the minimum for the common document tasks — is `~/.gitconfig` needed, is
  `~/.cache` needed for anything but package managers?
- Should the mode be per session, per turn, or per invocation?
- Does the fallback to unsandboxed execution undermine the guarantee, or is a loud warning enough?
- Is `--unshare-net` acceptable as a default, given that it breaks `git push` and `pip install`?

## Why this is LUNA

The core stays one program a single engineer can run locally. The sandbox is a policy passed to
a binary that is already on the machine, not a service, a daemon, or a container runtime. It
narrows the shell without adding a second shell implementation, and it keeps the existing
decision middleware as the human gate.

## Out of scope

- A TLS-terminating network proxy with per-host rules.
- A per-session user, a host firewall, or a container runtime.
- Windows and macOS backends.
- Resource limits (CPU, memory, disk) — cgroups, a separate card.
- Replacing `PathScope` for file tools; the sandbox covers the shell only.
