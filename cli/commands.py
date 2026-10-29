"""CLI slash-command handlers.

`handle_command` mutates the shared `state` (modes, current session) and returns whether
the loop should continue. The current runner lives in `state["session"]`, so /reset and
/undo talk to it directly.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from cli.config import SESSION_DIR, state
from cli.session import new_session
from core.agent.checkpoint import CheckpointManager
from core.agent.tools.fs_paths import FileAccessError
from core.agent.workspace import WorkspaceError, WorkspaceManager

# Name → (arguments, description). One list for /help and prompt completion.
COMMANDS: dict[str, tuple[str, str]] = {
    "/plan": ("", "plan only, write tools hidden"),
    "/act": ("", "carry out the task (write tools available)"),
    "/confirm": ("", "ask before each edit"),
    "/accept": ("", "apply edits without asking"),
    "/attach": ("<path> [--overwrite]", "copy a file from disk into attachments/"),
    "/fs": ("[path]", "workspace file tree"),
    "/cp": ("", "list checkpoints"),
    "/undo": ("<id>", "restore the workspace and history to a checkpoint"),
    "/reset": ("", "start a new empty session"),
    "/help": ("", "this help"),
    "/quit": ("", "quit"),
}


def checkpoint_manager() -> CheckpointManager:
    return CheckpointManager(
        workspace_path=SESSION_DIR / "workspace",
        checkpoints_root=SESSION_DIR / "checkpoints",
    )


def _workspace() -> WorkspaceManager:
    return WorkspaceManager(
        workspace_path=SESSION_DIR / "workspace",
        checkpoints_root=SESSION_DIR / "checkpoints",
    )


def _attach(arg: str) -> None:
    """`/attach <path> [--overwrite]` — copy a host file into the session.

    Goes through `WorkspaceManager` rather than writing the file itself: path policy, the
    size cap, and the snapshot after the write must match the HTTP upload, or exercising
    undo in the REPL says nothing about the server.
    """
    tokens = arg.split()
    overwrite = "--overwrite" in tokens
    # The path is the rest of the line, not the first token: Windows paths contain spaces.
    source = " ".join(t for t in tokens if t != "--overwrite").strip().strip("\"'")
    if not source:
        print("usage: /attach <path> [--overwrite]")
        return
    try:
        saved = _workspace().attach_from_host(Path(source), overwrite=overwrite)
    except (WorkspaceError, FileAccessError, OSError) as e:
        print(f"error: {e}")
        return
    print(
        f"attached: attachments/{saved.path} ({saved.size} bytes); "
        f"checkpoint {saved.checkpoint or '(none)'}"
    )


def _print_tree(root: Path, prefix: str = "") -> None:
    try:
        if not root.exists():
            print("(empty)")
            return
        for child in sorted(root.iterdir(), key=lambda c: (c.is_file(), c.name)):
            if child.name == ".runtime":
                continue
            print(f"{prefix}{child.name}{'/' if child.is_dir() else ''}")
            if child.is_dir():
                _print_tree(child, prefix + "  ")
    except OSError as e:
        print(f"could not list the tree: {e}")


def _wipe_session(session_root: Path) -> None:
    """Delete the session contents (SESSION_DIR) for a fresh start.

    repl.log lives one level up (in the process root SESSION_ROOT), so it is not deleted —
    but the walk skips it anyway, in case an old layout left the log inside the session
    (WinError 32 on an open file).
    """
    if not session_root.exists():
        return
    for child in session_root.iterdir():
        if child.name == "repl.log":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def handle_command(line: str) -> bool:
    """Handle a slash command. True — keep the loop going, False — quit.

    The current session is read and replaced in `state["session"]`.
    """
    cmd, *rest = line.split(maxsplit=1)
    arg = rest[0].strip() if rest else ""

    if cmd in ("/plan", "/act"):
        state["perm"] = cmd[1:]
    elif cmd == "/confirm":
        state["dec"] = "confirm"
    elif cmd == "/accept":
        state["dec"] = "accept_all"
    elif cmd == "/attach":
        _attach(arg)
    elif cmd == "/fs":
        _print_tree(SESSION_DIR / "workspace" / arg if arg else SESSION_DIR / "workspace")
    elif cmd == "/cp":
        snaps = checkpoint_manager().list()
        print("\n".join(f"{c.id}  ({c.created_at:%H:%M:%S})" for c in snaps) or "(no snapshots)")
    elif cmd == "/undo":
        if not arg:
            print("usage: /undo <checkpoint_id>")
        else:
            try:
                checkpoint_manager().restore(arg)
                agent = state["session"]._agent  # rehydrate: restore touches only the filesystem
                agent._messages = agent._store.load()
                print(f"restored to {arg}; history reloaded")
            except ValueError as e:
                print(f"error: {e}")
    elif cmd == "/reset":
        _wipe_session(SESSION_DIR)
        state["session"] = new_session()
        print("session reset")
    elif cmd in ("/help", "/?"):
        width = max(len(f"{name} {args}".strip()) for name, (args, _) in COMMANDS.items())
        print("commands:")
        for name, (args, desc) in COMMANDS.items():
            print(f"  {f'{name} {args}'.strip():<{width}}  {desc}")
        print(
            "keys: esc or ctrl+c — interrupt the turn; shift+tab — plan/act; "
            "alt+enter — new line; @ — a workspace file; ctrl+d — quit"
        )
    elif cmd == "/quit":
        return False
    else:
        print(f"unknown command {cmd!r}; /help")

    return True
