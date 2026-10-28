"""Слэш-команды CLI (хендлеры).

Диспетчер `handle_command` мутирует общий `state` (режимы, текущая сессия) и
возвращает флаг «продолжать цикл». Текущий раннер живёт в `state["session"]`, поэтому
/reset и /undo работают с ним напрямую, не гоняя сессию через сигнатуры.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from cli.config import SESSION_DIR, state
from cli.session import new_session
from core.agent.checkpoint import CheckpointManager
from core.agent.tools.fs_paths import FileAccessError
from core.agent.workspace import WorkspaceError, WorkspaceManager

# Имя → (аргументы, описание). Единый список для /help и автодополнения в приглашении.
COMMANDS: dict[str, tuple[str, str]] = {
    "/plan": ("", "только план, write-тулы скрыты"),
    "/act": ("", "выполнять задачу (write-тулы доступны)"),
    "/confirm": ("", "спрашивать перед каждой правкой"),
    "/accept": ("", "применять правки без вопросов"),
    "/attach": ("<path> [--overwrite]", "положить файл с диска в attachments/"),
    "/fs": ("[path]", "дерево файлов workspace"),
    "/cp": ("", "список чекпоинтов"),
    "/undo": ("<id>", "откатить workspace и историю к чекпоинту"),
    "/reset": ("", "новая сессия с чистого листа"),
    "/help": ("", "эта справка"),
    "/quit": ("", "выход"),
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
    """`/attach <путь> [--overwrite]` — прикрепить файл с хоста к сессии.

    Идёт через `WorkspaceManager`, а не своей записью в ФС: политика путей, кап размера и
    снимок после записи обязаны совпадать с HTTP-загрузкой, иначе обкатка undo в REPL
    перестанет говорить что-либо о сервере.
    """
    tokens = arg.split()
    overwrite = "--overwrite" in tokens
    # Путь берём остатком строки, а не первым токеном: пути на Windows содержат пробелы.
    source = " ".join(t for t in tokens if t != "--overwrite").strip().strip("\"'")
    if not source:
        print("использование: /attach <путь> [--overwrite]")
        return
    try:
        saved = _workspace().attach_from_host(Path(source), overwrite=overwrite)
    except (WorkspaceError, FileAccessError, OSError) as e:
        print(f"ошибка: {e}")
        return
    print(
        f"прикреплено: attachments/{saved.path} ({saved.size} байт); "
        f"чекпоинт {saved.checkpoint or '(нет)'}"
    )


def _print_tree(root: Path, prefix: str = "") -> None:
    try:
        if not root.exists():
            print("(пусто)")
            return
        for child in sorted(root.iterdir(), key=lambda c: (c.is_file(), c.name)):
            if child.name == ".runtime":
                continue
            print(f"{prefix}{child.name}{'/' if child.is_dir() else ''}")
            if child.is_dir():
                _print_tree(child, prefix + "  ")
    except OSError as e:
        print(f"не удалось вывести дерево: {e}")


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
    elif cmd == "/attach":
        _attach(arg)
    elif cmd == "/fs":
        _print_tree(SESSION_DIR / "workspace" / arg if arg else SESSION_DIR / "workspace")
    elif cmd == "/cp":
        snaps = checkpoint_manager().list()
        print("\n".join(f"{c.id}  ({c.created_at:%H:%M:%S})" for c in snaps) or "(нет снимков)")
    elif cmd == "/undo":
        if not arg:
            print("использование: /undo <checkpoint_id>")
        else:
            try:
                checkpoint_manager().restore(arg)
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
        width = max(len(f"{name} {args}".strip()) for name, (args, _) in COMMANDS.items())
        print("команды:")
        for name, (args, desc) in COMMANDS.items():
            print(f"  {f'{name} {args}'.strip():<{width}}  {desc}")
        print(
            "клавиши: esc или ctrl+c — прервать ход; shift+tab — plan/act; "
            "alt+enter — новая строка; @ — файл из workspace; ctrl+d — выход"
        )
    elif cmd == "/quit":
        return False
    else:
        print(f"неизвестная команда {cmd!r}; /help")

    return True
