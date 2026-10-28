"""
File tools of the flat pool: read, write, edit, ls, find, grep, rm.

They work on ordinary files of the agent's zone without a shell, so the tool itself enforces
zone scoping, the checkpoint (@with_checkpoint), the `.runtime/` block and the output limit
(@truncate_output). The zone comes from
the runtime context (get_runtime), so the same tools serve the MainAgent (workspace/) and a
subagent (its own zone + a read-only view of workspace).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from pathlib import Path

from langgraph.runtime import get_runtime

from core.agent.checkpoint import with_checkpoint
from core.agent.tools import agent_tool
from core.agent.tools.fs_paths import HIDDEN_DIRS, FileAccessError, PathScope
from core.agent.tools.tool_output import cut_window, truncate_output

_NOTHING_FOUND = "(ничего не найдено)"


def _scope() -> PathScope:
    """PathScope of the current agent, built from the runtime context."""
    return PathScope.from_context(get_runtime().context)


def _read_text(p: Path, path: str) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise FileAccessError(f"не текстовый файл или неверная кодировка: {path!r}") from e
    except OSError as e:
        raise FileAccessError(f"не удалось прочитать {path!r}: {e}") from e


def _zone_files(root: Path, select: Callable[[Path], Iterator[Path]]) -> list[Path]:
    """Files picked by `select(root)`, sorted, inside `root` and outside hidden directories.

    A glob pattern may climb out of `root` with `..`, so every hit is checked after resolving.
    """
    base = root.resolve()
    files = []
    for f in select(root):
        if not f.is_file():
            continue
        try:
            rel = f.resolve().relative_to(base)
        except ValueError:
            continue
        if HIDDEN_DIRS.intersection(rel.parts):
            continue
        files.append(f.resolve())
    return sorted(set(files))


def _rel(f: Path, root: Path) -> str:
    return f.relative_to(root.resolve()).as_posix()


# --- reading (is_write=False) --------------------------------------------------------


@agent_tool(is_write=False)
async def read(path: str, offset: int | None = None, limit: int | None = None) -> str:
    """Read a text file from the working zone. The path is relative (e.g. 'notes/working.md').

    Without offset and limit — the whole file. offset is the first line number (from 1),
    limit is how many lines to return. With a window the answer starts with the header
    «[строки X–Y из N]». A large answer is cut, and the note at its end gives the offset to
    continue from.
    """
    p = _scope().resolve_read(path)
    if not p.is_file():
        raise FileAccessError(f"файл не найден: {path!r}")
    text = _read_text(p, path)
    lines = text.splitlines(keepends=True)
    total = len(lines)
    windowed = offset is not None or limit is not None

    start = 1 if offset is None else offset
    if windowed:
        if start < 1 or (limit is not None and limit < 1):
            raise FileAccessError("offset и limit должны быть не меньше 1")
        if start > total:
            raise FileAccessError(f"offset {start} за концом файла: в {path!r} строк {total}")
    end = total if limit is None else min(total, start + limit - 1)
    body = "".join(lines[start - 1 : end]) if windowed else text

    cut = cut_window(body, start, total)
    if cut is not None:
        return cut
    return f"[строки {start}–{end} из {total}]\n{body}" if windowed else body


@agent_tool(is_write=False)
@truncate_output("head")
async def ls(path: str = ".") -> str:
    """List the files in a directory (one level). The path is relative (e.g. 'notes/')."""
    d = _scope().resolve_read(path)
    if not d.is_dir():
        raise FileAccessError(f"директория не найдена: {path!r}")
    # Directories first, then files; service directories (.runtime/, spilled output) hidden.
    entries = [
        child.name + ("/" if child.is_dir() else "")
        for child in sorted(d.iterdir(), key=lambda c: (c.is_file(), c.name))
        if child.name not in HIDDEN_DIRS
    ]
    return "\n".join(entries) if entries else "(пусто)"


@agent_tool(is_write=False)
@truncate_output("head")
async def find(pattern: str, path: str = ".") -> str:
    """Find files by name with a glob pattern: '*.md' — in the directory, '**/*.md' — in all
    subdirectories, '*report*' — by part of the name. path is the directory to search from;
    paths in the answer are relative to it.
    """
    root = _scope().resolve_read(path)
    if not root.is_dir():
        raise FileAccessError(f"не каталог: {path!r}")
    try:
        files = _zone_files(root, lambda r: r.glob(pattern))
    except (NotImplementedError, ValueError) as e:
        raise FileAccessError(f"неверный шаблон {pattern!r}: {e}") from e
    return "\n".join(_rel(f, root) for f in files) if files else _NOTHING_FOUND


@agent_tool(is_write=False)
@truncate_output("head")
async def grep(
    pattern: str, path: str = ".", glob: str | None = None, ignore_case: bool = False
) -> str:
    """Find lines by content with a regular expression (Python re).

    path is a directory or a single file. glob narrows the set of files (e.g. '*.md'). Each
    match is a 'path:line_number: text' line; the line number works as offset for read.
    """
    try:
        regex = re.compile(pattern, re.IGNORECASE if ignore_case else 0)
    except re.error as e:
        raise FileAccessError(f"неверная регулярка {pattern!r}: {e}") from e

    target = _scope().resolve_read(path)
    if target.is_file():
        root = target.parent
        files = [target.resolve()]
    elif target.is_dir():
        root = target
        files = _zone_files(root, lambda r: r.rglob(glob or "*"))
    else:
        raise FileAccessError(f"не найдено: {path!r}")

    hits: list[str] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable files are skipped silently
        rel = _rel(f, root)
        for i, line in enumerate(text.splitlines(), 1):
            if regex.search(line):
                hits.append(f"{rel}:{i}: {line}")
    return "\n".join(hits) if hits else _NOTHING_FOUND


# --- writing (is_write=True, triggers a checkpoint) ----------------------------------


@agent_tool(is_write=True)
@with_checkpoint()
async def write(path: str, content: str) -> str:
    """Create or overwrite a file in the working zone (RW). The path is relative."""
    p = _scope().resolve_write(path)  # the resolver's FileAccessError passes through as is
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as e:  # only a real IO failure, with the original
        raise FileAccessError(f"не удалось записать {path!r}: {e}") from e
    return f"Записано: {path} ({len(content)} символов)"


@agent_tool(is_write=True)
@with_checkpoint()
async def edit(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Replace old_string with new_string in an existing file.

    By default old_string must occur EXACTLY ONCE (otherwise an error — add more context).
    replace_all=True replaces every occurrence.
    """
    p = _scope().resolve_write(path)
    if not p.is_file():
        raise FileAccessError(f"файл не найден: {path!r}")
    text = _read_text(p, path)

    count = text.count(old_string)
    if count == 0:
        raise FileAccessError(f"old_string не найден в {path!r}")
    if count > 1 and not replace_all:
        raise FileAccessError(
            f"old_string встречается {count} раз в {path!r}; уточните контекст или replace_all=True"
        )
    try:
        p.write_text(text.replace(old_string, new_string), encoding="utf-8")
    except OSError as e:
        raise FileAccessError(f"не удалось записать {path!r}: {e}") from e
    return f"Изменён: {path} (замен: {count if replace_all else 1})"


@agent_tool(is_write=True)
@with_checkpoint()
async def rm(path: str) -> str:
    """Delete a file from the working zone (RW). Directories are not deleted."""
    p = _scope().resolve_write(path)
    if not p.is_file():
        raise FileAccessError(f"файл не найден: {path!r}")
    try:
        p.unlink()
    except OSError as e:
        raise FileAccessError(f"не удалось удалить {path!r}: {e}") from e
    return f"Удалён: {path}"
