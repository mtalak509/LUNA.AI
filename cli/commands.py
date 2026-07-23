"""Слэш-команды CLI (хендлеры).

Диспетчер `handle_command` мутирует общий `state` (режимы, ptr, текущая сессия) и
возвращает флаг «продолжать цикл». Текущий раннер живёт в `state["session"]`, поэтому
/reset и /undo работают с ним напрямую, не гоняя сессию через сигнатуры.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from cli.config import SESSION_DIR, state
from cli.session import new_session
from core.agent.checkpoint import CheckpointManager


def _manager() -> CheckpointManager:
    return CheckpointManager(
        workspace_path=SESSION_DIR / "workspace",
        checkpoints_root=SESSION_DIR / "checkpoints",
    )


def _print_tree(root: Path, prefix: str = "") -> None:
    if not root.exists():
        print("(пусто)")
        return
    for child in sorted(root.iterdir(), key=lambda c: (c.is_file(), c.name)):
        if child.name == ".runtime":
            continue
        print(f"{prefix}{child.name}{'/' if child.is_dir() else ''}")
        if child.is_dir():
            _print_tree(child, prefix + "  ")


def _wipe_session(session_root: Path) -> None:
    """Снести содержимое сессии (SESSION_DIR) для свежего старта.

    repl.log живёт УРОВНЕМ ВЫШЕ (в корне процесса SESSION_ROOT), поэтому под снос не
    попадает — но проходим по-детально с пропуском на случай, если кто-то запустил CLI
    из старой раскладки, где лог лежал внутри сессии (WinError 32 на открытом файле).
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
    """Обработать слэш-команду. Вернуть True — продолжать цикл, False — выйти.

    Текущая сессия берётся/пересоздаётся в `state["session"]`.
    """
    cmd, *rest = line.split(maxsplit=1)
    arg = rest[0].strip() if rest else ""

    if cmd in ("/plan", "/act"):
        state["perm"] = cmd[1:]
    elif cmd == "/confirm":
        state["dec"] = "confirm"
    elif cmd == "/accept":
        state["dec"] = "accept_all"
    elif cmd == "/ptr":
        # Липкий, как режимы: шлётся с КАЖДЫМ следующим ходом до смены/сброса —
        # эмуляция фронта, который передаёт открытый раздел с каждым turn.
        state["ptr"] = arg or None
        print(f"pointer: {state['ptr'] or '(сброшен)'}")
    elif cmd == "/fs":
        _print_tree(SESSION_DIR / "workspace" / arg if arg else SESSION_DIR / "workspace")
    elif cmd == "/cp":
        snaps = _manager().list()
        print("\n".join(f"{c.id}  ({c.created_at:%H:%M:%S})" for c in snaps) or "(нет снимков)")
    elif cmd == "/undo":
        if not arg:
            print("использование: /undo <checkpoint_id>")
        else:
            try:
                _manager().restore(arg)
                agent = state["session"]._agent  # ре-гидратация: restore трогает только ФС
                agent._messages = agent._store.load()
                print(f"откат до {arg}; история перезагружена")
            except ValueError as e:
                print(f"ошибка: {e}")
    elif cmd == "/reset":
        _wipe_session(SESSION_DIR)
        state["session"] = new_session()
        print("сессия пересоздана")
    elif cmd in ("/help", "/?"):
        print(
            "команды: /plan /act  /confirm /accept  /ptr [pointer]  /fs [path]  "
            "/cp  /undo <id>  /reset  /quit"
        )
    elif cmd == "/quit":
        return False
    else:
        print(f"неизвестная команда {cmd!r}; /help")

    return True
