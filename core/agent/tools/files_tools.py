"""
Шесть файловых тулов плоского пула: read/write/edit/list/search/delete.

Работа с обычными файлами рабочей зоны агента — аналог Read/Write/Edit/Glob/Grep, но НЕ
через shell: именно в туле навешиваются скоуп зон, чекпоинт (@with_checkpoint)
 и блок .runtime/. Зона определяется в рантайме из контекста (get_runtime), поэтому одни
и те же тулы обслуживают и MainAgent (workspace/), и субагента (своя зона + RO-вид workspace).
"""

from __future__ import annotations

from langgraph.runtime import get_runtime

from core.agent.checkpoint import with_checkpoint
from core.agent.tools import agent_tool
from core.agent.tools.fs_paths import FileAccessError, PathScope

# Имя runtime-каталога, скрываемого из листинга/поиска (резолвер и так блокирует вход).
_RUNTIME_DIRNAME = ".runtime"
_MAX_SEARCH_RESULTS = 100
_MAX_SEARCH_FILE_BYTES = 1_000_000


def _scope() -> PathScope:
    """Получить PathScope для текущего агента из рантайм-контекста."""
    context = get_runtime().context
    return PathScope(
        agent_scope=context.agent_scope,
        workspace_path=context.workspace_path,
        subagent_path=context.subagent_path,
    )


# --- чтение (is_write=False) --------------------------------------------------------


@agent_tool(is_write=False)
async def read_file(path: str) -> str:
    """Прочитать текстовый файл из рабочей зоны. Путь относительный (напр. 'notes/working.md')."""
    p = _scope().resolve_read(path)
    if not p.is_file():
        raise FileAccessError(f"файл не найден: {path!r}")
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise FileAccessError(f"не текстовый файл или неверная кодировка: {path!r}") from e
    except OSError as e:
        raise FileAccessError(f"не удалось прочитать {path!r}: {e}") from e


@agent_tool(is_write=False)
async def list_files(path: str = ".") -> str:
    """Получить список файлов в директории. Путь относительный (напр. 'notes/')."""
    d = _scope().resolve_read(path)
    if not d.is_dir():
        raise FileAccessError(f"директория не найдена: {path!r}")
    # Каталоги сначала, потом файлы; .runtime/ скрыт (шум + защита от самопохищения контекста).
    entries = [
        child.name + ("/" if child.is_dir() else "")
        for child in sorted(d.iterdir(), key=lambda c: (c.is_file(), c.name))
        if child.name != _RUNTIME_DIRNAME
    ]
    return "\n".join(entries) if entries else "(пусто)"


@agent_tool(is_write=False)
async def search_files(query: str, path_hint: str = ".") -> str:
    """Найти файлы в зоне по подстроке в ИМЕНИ или СОДЕРЖИМОМ. path_hint сужает поддерево."""
    if query.strip() == "":
        raise FileAccessError("пустой запрос")
    root = _scope().resolve_read(path_hint)
    if not root.is_dir():
        raise FileAccessError(f"не каталог: {path_hint!r}")

    needle = query.lower()
    hits: list[str] = []
    for f in sorted(root.rglob("*")):
        if _RUNTIME_DIRNAME in f.parts or not f.is_file():
            continue
        rel = f.relative_to(root)
        if needle in f.name.lower():
            hits.append(str(rel))
        else:
            try:
                if f.stat().st_size > _MAX_SEARCH_FILE_BYTES:
                    continue
                text = f.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # бинарь/недоступный — пропускаем молча
            for i, line in enumerate(text.splitlines(), 1):
                if needle in line.lower():
                    hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                    break
        if len(hits) >= _MAX_SEARCH_RESULTS:
            hits.append("... (результаты усечены)")
            break
    return "\n".join(hits) if hits else "(ничего не найдено)"


# --- запись (is_write=True, триггерят чекпоинт) -------------------------------------


@agent_tool(is_write=True)
@with_checkpoint()
async def write_file(path: str, content: str) -> str:
    """Создать или перезаписать файл в рабочей зоне (RW). Путь относительный."""
    p = _scope().resolve_write(path)          # FileAccessError резолвера проходит как есть
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    except OSError as e:                       # только реальный IO-сбой, с оригиналом
        raise FileAccessError(f"не удалось записать {path!r}: {e}") from e
    return f"Записано: {path} ({len(content)} символов)"


@agent_tool(is_write=True)
@with_checkpoint()
async def edit_file(path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    """Заменить old_string на new_string в существующем файле.

    По умолчанию old_string обязан встречаться РОВНО ОДИН раз (иначе ошибка — уточните
    контекст). replace_all=True заменяет все вхождения.
    """
    p = _scope().resolve_write(path)
    if not p.is_file():
        raise FileAccessError(f"файл не найден: {path!r}")
    try:
        text = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError) as e:
        raise FileAccessError(f"не удалось прочитать {path!r}: {e}") from e

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
async def delete_file(path: str) -> str:
    """Удалить файл из рабочей зоны (RW). Каталоги не удаляются."""
    p = _scope().resolve_write(path)
    if not p.is_file():
        raise FileAccessError(f"файл не найден: {path!r}")
    try:
        p.unlink()
    except OSError as e:
        raise FileAccessError(f"не удалось удалить {path!r}: {e}") from e
    return f"Удалён: {path}"