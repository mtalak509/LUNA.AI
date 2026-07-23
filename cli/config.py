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

# Единый разделяемый словарь состояния. Модули делают `from cli.config import state`
# и мутируют его ПО КЛЮЧУ (никогда не переприсваивают сам dict) — поэтому изменения
# из одного модуля видны во всех остальных (один и тот же объект по ссылке).
#   perm: plan|act — Plan прячет write-тулы (PermissionMiddleware).
#   dec:  confirm|accept_all — confirm гейтит любой write-тул на подтверждение (DecisionMiddleware).
#   ptr:  JSON Pointer «раздела, открытого в UI»; в проде шлёт фронт, тут — команда /ptr;
#         None = не слать.
#   procedures_root: procedures_root процесса; ставится один раз в app.main() через init_process.
#   session: текущий раннер AgentSession; пересоздаётся на /reset.
state: dict = {
    "perm": "act",
    "dec": "accept_all",
    "ptr": None,
    "procedures_root": None,
    "session": None,
}
