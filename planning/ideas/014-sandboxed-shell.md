# 014. Sandboxed shell via bubblewrap

- id: 014
- status: shaped
- source: own
- value: high

## Why

Card 003 gave the shell full host-user rights and stated the tradeoff plainly: `PathScope` does
not apply inside the child process, so the shell can leave the zone, read `.runtime`, and write
anywhere the user can. That was accepted because a second, locked-down shell did not belong in
the core.

The cost of that decision is now visible. File tools are jailed by `PathScope`; the shell is not.
A document can carry injected instructions, and the shell will execute them with the user's full
rights — including network access, `~/.ssh`, and every other repository on the machine.

The shell should still be able to see the machine. A shell that can never leave the zone is the
wrong product: ordinary git, formatters and scripts read configuration outside the project. The
boundary that matters is writes, secrets and the network. This card puts that boundary in
bubblewrap on Linux. It deliberately reopens the "no jail inside the LUNA process" line of
card 003.

## What changes

`spawn_posix_shell` keeps one bash, with no command filter. `safe` and `normal` start it through
`bwrap`. `bypass` starts it exactly as today, with no `bwrap`.

The sandbox is a mount policy. What is not mounted is not there. The process keeps the user's
uid and gains no rights inside a mounted path. Shared flags:

- `--proc /proc`, `--dev /dev`, `--tmpfs /tmp` (a private empty temp, not the host `/tmp`);
- `--unshare-net` in both sandboxed modes;
- `--die-with-parent` so a killed LUNA does not leave orphans;
- `--new-session` to detach from the controlling terminal.

Checked on this host: `bwrap` 0.9.0 at `/usr/bin/bwrap`, `kernel.unprivileged_userns_clone = 1`,
and a sandboxed `sh` ran as uid 1000 with `/home/maxim` absent and `/usr` read-only.

Three session modes. The default is `normal`.

| Mode | Disk the command can see | Writes | Secrets | Network |
|---|---|---|---|---|
| `safe` | the zone, plus read-only `/usr`, `/lib`, `/lib64`, `/bin`, `/etc` | the zone | absent with the rest of the disk | no |
| `normal` | the whole machine, read-only except the zone | the zone and the private `/tmp` | masked | no |
| `bypass` | the host, as the user | the host | visible | yes |

`safe` is for work that must stay inside the directory LUNA was started in (the agent zone).
System programs are visible so `bash`, `git` and `python` can run. Home, other repositories and
the network are not there.

`normal` is the everyday shell. The root is mounted read-only and the zone is mounted read-write
on top, at the same path, so a later mount wins. Local git works: `status`, `diff` and `commit`
write inside the zone, and `~/.gitconfig` stays readable. `git push`, `git pull`, package
installs and anything else that needs the network or a credential do not get a special mount.
They belong in `bypass`.

`normal` masks these paths so the command can neither read nor write them:

- `~/.ssh`, `~/.gnupg`, `~/.aws`;
- `~/.netrc`, `~/.git-credentials`;
- token directories under `~/.config`, starting with `~/.config/gh`;
- the zone's `.runtime`;
- the Docker socket (`/var/run/docker.sock`).

`bypass` is a session switch, not a question asked on every command. It drops the mount policy
and `--unshare-net` together: full filesystem, secrets and network, for the rest of the session.
The default stays `normal`. There is no fourth "network" mode and no per-command prompt to leave
the sandbox. Asking on every `git push` is the behaviour this mode exists to avoid.

The mode is orthogonal to Plan/Act and to Confirm/accept-all. Plan still hides the tool.
Confirm still asks before every invocation; accept-all does not. `bypass` removes the sandbox,
not those questions. `bypass` together with accept-all is an unsandboxed shell that does not
ask. Subagents inherit the session's sandbox mode as they inherit its decision mode.

If `bwrap` is missing or unusable, `safe` and `normal` cannot be enforced. The command runs as
today's host shell and the result says so. It must not look as though the sandbox held.

## Why this is LUNA

The core stays one program a single engineer can run locally. The sandbox is a policy passed to
a binary that is already on the machine, not a service, a daemon, or a container runtime. It
does not add a second shell, a per-command network gate, or a proxy. Confirm remains the human
gate it already is.

## Out of scope

- A TLS-terminating network proxy with per-host rules.
- A per-command approval that turns the network on, or that reveals one secret path, while
  leaving the rest of the mode in place.
- A per-session user, a host firewall, or a container runtime.
- Windows and macOS backends. Native PowerShell stays the host shell from card 003. A Windows
  sandbox is a separate card: there is no `bwrap` there, and AppContainer is a different
  mechanism.
- Resource limits (CPU, memory, disk) — cgroups, a separate card.
- Replacing `PathScope` for file tools. The sandbox covers the shell only.
- A read-only zone mode. Plan already hides the shell.

## Recorded decision this amends

Accepting this card amends card 003. "Any other jail inside the LUNA process" no longer covers
the Linux shell. The Windows shell stays as card 003 wrote it until its own card.

## Drawbacks

- **`normal` is not a jail around the project.** The command can read other repositories and
  `~/Documents`. It cannot write them, and it cannot read the masked secrets. A user who wanted
  the disk hidden uses `safe`.
- **Missing paths look like a missing file.** A masked secret or an unmounted directory surfaces
  as "No such file or directory", not as "blocked by policy". The same trap hid `awk` in a test
  when `/etc` was not mounted.
- **Ubuntu 24.04 restricts unprivileged user namespaces.** This host has
  `kernel.apparmor_restrict_unprivileged_userns = 1`. `bwrap` 0.9.0 ships an AppArmor profile
  that is expected to cover it. That still has to be verified on a clean install.
- **Linux only.** The contract above is the POSIX shell. macOS would need Seatbelt. Windows
  would need its own mechanism, and that work is larger than an argv in front of `bash`.
- **Nested sandboxes.** Running LUNA inside Docker, or running `docker` from inside the sandbox,
  needs care: user namespaces do not nest freely. The Docker socket stays masked in `normal`
  because mounting it is a privilege escalation.
- **Not a resource limit.** A fork bomb inside the sandbox is still a fork bomb.
- **Network is the whole session or nothing.** `--unshare-net` has no host list. Per-host
  filtering needs a proxy, which Claude Code does with a TLS-terminating proxy and its own CA.
  That stays out of this card. A session that needs `git push` switches to `bypass` and takes
  the rest of the fence down with the network.
- **A loud fallback is still unsandboxed.** If `bwrap` is absent, `safe` and `normal` do not
  hold. The result says so, and the command has still run as the user.
