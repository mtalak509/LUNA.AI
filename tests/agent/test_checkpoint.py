"""
Unit-тесты: CheckpointManager (снимок/откат тома) и декоратор @with_checkpoint.

Без сети и LLM. Том — temp-каталоги; рантайм-контекст для декоратора подменяется через
monkeypatch на get_runtime (граф create_agent не поднимаем).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent import checkpoint as cp
from core.agent.checkpoint import CheckpointManager, with_checkpoint


@pytest.fixture()
def session(tmp_path: Path) -> tuple[Path, Path]:
    """(workspace, checkpoints_root). workspace заполнен: notes/ + .runtime/."""
    workspace = tmp_path / "session" / "workspace"
    checkpoints = tmp_path / "checkpoints"
    (workspace / "notes").mkdir(parents=True)
    (workspace / "notes" / "a.md").write_text("A", encoding="utf-8")
    (workspace / ".runtime").mkdir()
    (workspace / ".runtime" / "messages.jsonl").write_text("{}", encoding="utf-8")
    return workspace, checkpoints


# --- CheckpointManager: конструктор --------------------------------------------------


def test_ctor_rejects_checkpoints_inside_workspace(session: tuple[Path, Path]) -> None:
    workspace, _ = session
    with pytest.raises(ValueError):
        CheckpointManager(workspace_path=workspace, checkpoints_root=workspace / "cp")


# --- snapshot ------------------------------------------------------------------------


def test_snapshot_copies_workspace_including_runtime(session: tuple[Path, Path]) -> None:
    workspace, checkpoints = session
    mgr = CheckpointManager(workspace_path=workspace, checkpoints_root=checkpoints)
    dest = mgr.snapshot("initial")
    assert dest.name == "c000_initial"
    assert (dest / "notes" / "a.md").read_text(encoding="utf-8") == "A"
    # .runtime/ обязан попасть в снимок (иначе диалог «уедет в будущее» при откате).
    assert (dest / ".runtime" / "messages.jsonl").exists()


def test_snapshot_excludes_subagents(tmp_path: Path) -> None:
    """subagents/ — сосед workspace, не его потомок → в снимок не попадает."""
    workspace = tmp_path / "session" / "workspace"
    workspace.mkdir(parents=True)
    (workspace / "x.md").write_text("x", encoding="utf-8")
    subagents = tmp_path / "session" / "subagents" / "uid"
    subagents.mkdir(parents=True)
    (subagents / "secret.md").write_text("s", encoding="utf-8")

    mgr = CheckpointManager(workspace_path=workspace, checkpoints_root=tmp_path / "checkpoints")
    dest = mgr.snapshot("snap")
    assert (dest / "x.md").exists()
    assert not (dest / "secret.md").exists()
    assert not (dest / "subagents").exists()


def test_snapshot_monotonic_numbering(session: tuple[Path, Path]) -> None:
    workspace, checkpoints = session
    mgr = CheckpointManager(workspace_path=workspace, checkpoints_root=checkpoints)
    assert mgr.snapshot("one").name == "c000_one"
    assert mgr.snapshot("two").name == "c001_two"
    assert mgr.snapshot("three").name == "c002_three"


def test_snapshot_slugifies_label(session: tuple[Path, Path]) -> None:
    workspace, checkpoints = session
    mgr = CheckpointManager(workspace_path=workspace, checkpoints_root=checkpoints)
    dest = mgr.snapshot("после  правки маршрута")
    assert dest.name == "c000_после_правки_маршрута"


# --- restore -------------------------------------------------------------------------


def test_restore_replaces_workspace(session: tuple[Path, Path]) -> None:
    workspace, checkpoints = session
    mgr = CheckpointManager(workspace_path=workspace, checkpoints_root=checkpoints)
    snap = mgr.snapshot("base")

    # мутируем workspace после снимка
    (workspace / "notes" / "a.md").write_text("MUTATED", encoding="utf-8")
    (workspace / "extra.md").write_text("new", encoding="utf-8")

    mgr.restore(snap.name)

    assert (workspace / "notes" / "a.md").read_text(encoding="utf-8") == "A"
    assert not (workspace / "extra.md").exists()  # правки после снимка стёрты


def test_restore_unknown_id_raises(session: tuple[Path, Path]) -> None:
    workspace, checkpoints = session
    mgr = CheckpointManager(workspace_path=workspace, checkpoints_root=checkpoints)
    with pytest.raises(ValueError):
        mgr.restore("c999_nope")


def test_next_number_survives_restore_without_collision(session: tuple[Path, Path]) -> None:
    """После отката номер НЕ откатывается: новый снимок = c003, а не перезапись c001."""
    workspace, checkpoints = session
    mgr = CheckpointManager(workspace_path=workspace, checkpoints_root=checkpoints)
    mgr.snapshot("c0")   # c000
    mgr.snapshot("c1")   # c001
    mgr.snapshot("c2")   # c002
    mgr.restore("c000_c0")
    assert mgr.snapshot("after_undo").name == "c003_after_undo"


# --- list ----------------------------------------------------------------------------


def test_list_empty_when_no_storage(session: tuple[Path, Path]) -> None:
    workspace, checkpoints = session
    mgr = CheckpointManager(workspace_path=workspace, checkpoints_root=checkpoints)
    assert mgr.list() == []


def test_list_sorted_by_number(session: tuple[Path, Path]) -> None:
    workspace, checkpoints = session
    mgr = CheckpointManager(workspace_path=workspace, checkpoints_root=checkpoints)
    mgr.snapshot("first")
    mgr.snapshot("second")
    infos = mgr.list()
    assert [c.n for c in infos] == [0, 1]
    assert [c.label for c in infos] == ["first", "second"]
    assert infos[0].id == "c000_first"


def test_list_ignores_foreign_dirs(session: tuple[Path, Path]) -> None:
    workspace, checkpoints = session
    mgr = CheckpointManager(workspace_path=workspace, checkpoints_root=checkpoints)
    mgr.snapshot("real")
    (checkpoints / "not_a_checkpoint").mkdir()
    assert [c.id for c in mgr.list()] == ["c000_real"]


# --- декоратор @with_checkpoint ------------------------------------------------------


def _patch_runtime(
    monkeypatch: pytest.MonkeyPatch, *, workspace: Path, checkpoints: Path | None
) -> None:
    """Подменяет get_runtime в модуле checkpoint, возвращая фейковый runtime с .context."""
    ctx = SimpleNamespace(workspace_path=workspace, checkpoints_root=checkpoints)
    monkeypatch.setattr(cp, "get_runtime", lambda *a, **k: SimpleNamespace(context=ctx))


async def test_decorator_snapshots_on_success(
    session: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, checkpoints = session
    _patch_runtime(monkeypatch, workspace=workspace, checkpoints=checkpoints)

    @with_checkpoint()
    async def write_file(path: str, content: str) -> str:
        (workspace / path).write_text(content, encoding="utf-8")
        return "ok"

    result = await write_file(path="b.md", content="B")
    assert result == "ok"
    assert [c.id for c in CheckpointManager(
        workspace_path=workspace, checkpoints_root=checkpoints
    ).list()] == ["c000_write_file"]  # label по умолчанию = имя функции


async def test_decorator_no_snapshot_on_exception(
    session: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, checkpoints = session
    _patch_runtime(monkeypatch, workspace=workspace, checkpoints=checkpoints)

    @with_checkpoint()
    async def failing_tool() -> str:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await failing_tool()
    assert not checkpoints.exists()  # failed tool call → чекпоинта нет


async def test_decorator_label_str_and_callable(
    session: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, checkpoints = session
    _patch_runtime(monkeypatch, workspace=workspace, checkpoints=checkpoints)

    @with_checkpoint(label="static_label")
    async def t_str() -> str:
        return "ok"

    @with_checkpoint(label=lambda kw: f"edit_{kw['path']}")
    async def t_dyn(path: str) -> str:
        return "ok"

    await t_str()
    await t_dyn(path="notes/x.md")
    labels = {c.label for c in CheckpointManager(
        workspace_path=workspace, checkpoints_root=checkpoints
    ).list()}
    # slug каталога выкидывает '/' и '.' (FS-safe) → "edit_notes/x.md" → "edit_notesxmd".
    assert labels == {"static_label", "edit_notesxmd"}


async def test_decorator_noop_without_checkpoints_root(
    session: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, checkpoints = session
    _patch_runtime(monkeypatch, workspace=workspace, checkpoints=None)

    @with_checkpoint()
    async def tool() -> str:
        return "ok"

    assert await tool() == "ok"
    assert not checkpoints.exists()  # инертен, но не падает


async def test_decorator_snapshot_failure_does_not_break_tool(
    session: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace, checkpoints = session
    _patch_runtime(monkeypatch, workspace=workspace, checkpoints=checkpoints)

    # снимок падает — тул всё равно обязан вернуть результат (write уже состоялся)
    def _boom(self: CheckpointManager, label: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(CheckpointManager, "snapshot", _boom)

    @with_checkpoint()
    async def tool() -> str:
        return "ok"

    assert await tool() == "ok"
