"""Shared CLI state and session layout.

Layout (like the multi-session server, but with one fixed session): SESSION_ROOT is the
PROCESS root (procedures/ + repl.log), SESSION_DIR is the SESSION root (workspace/
checkpoints/subagents). The CLI is single-session, so the id is fixed. The root lives
under LUNA_HOME as `~/.luna/cli` — a sibling of the WEB root `~/.luna/sessions`, so the
two interfaces never clobber each other's on-disk state. The path is fixed, not env-set.
"""

from __future__ import annotations

from core.config import LUNA_HOME

SESSION_ROOT = LUNA_HOME / "cli"
SESSION_ID = "dev"
SESSION_PROFILE = "standalone"
SESSION_DIR = SESSION_ROOT / SESSION_ID

# One shared state dict. Modules do `from cli.config import state` and mutate it by key
# (never rebind the dict), so a change in one module is visible in the others.
#   perm: plan|act — plan hides write tools (PermissionMiddleware).
#   dec:  confirm|accept_all — confirm gates every write tool (DecisionMiddleware).
#   procedures_root: the process procedures root; set once in app.main() via init_process.
#   session: the current AgentSession runner; replaced on /reset.
state: dict = {
    "perm": "act",
    "dec": "accept_all",
    "procedures_root": None,
    "session": None,
}
