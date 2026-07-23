"""
Тесты обработчика `delegate_to_subagent`.

Без сети и без живого LLM: субагентская петля мокается — `get_subagent_factory()`
подменяется на стаб, чей `build()` отдаёт фейкового субагента с заранее заданным
`run_stream` (async-генератор `StreamEvent`) и `final_text`. Проверяем ОБВЯЗКУ
(зона/чекпоинт-ПЕРЕД/мультиплекс/сборка контракта/краш-изоляция), не рассуждение модели.

Рантайм-контекст (`get_runtime`), стрим-writer (`get_stream_writer`) и `CheckpointManager`
монкипатчатся в модуле `delegation_tools` — реальный путь вызова тула через `.ainvoke`.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import core.agent.tools.delegation_tools as dt
from core.agent.base import StreamEvent
from core.agent.tools import is_main_only, is_write_tool
from core.agent.tools.delegation_tools import DelegationError, delegate_to_subagent

# --- стабы субагента и фабрики ------------------------------------------------


class _StubSub:
    """Фейковый субагент: отдаёт заданные события и (опционально) пишет файл в artifacts/."""

    def __init__(self, *, events, final_text, artifacts_dir=None, write_file=None, raises=None, order=None):
        self._events = events
        self.final_text = final_text
        self._artifacts_dir = artifacts_dir
        self._write_file = write_file
        self._raises = raises
        self._order = order
        self.ran_with = None

    async def run_stream(self, task_text, *, permission_mode, decision_mode, session_id):
        if self._order is not None:
            self._order.append("run")
        self.ran_with = dict(
            task_text=task_text, permission_mode=permission_mode,
            decision_mode=decision_mode, session_id=session_id,
        )
        if self._raises is not None:
            raise self._raises
        # Имитация write_file субагента: deliverable появляется в его artifacts/.
        if self._write_file is not None and self._artifacts_dir is not None:
            self._artifacts_dir.mkdir(parents=True, exist_ok=True)
            (self._artifacts_dir / self._write_file).write_text("1..10", encoding="utf-8")
        for ev in self._events:
            yield ev


class _StubFactory:
    """Стаб фабрики: процессная, без сессионных путей (15.2) — зона приходит в build()."""

    def __init__(self, *, procedures_root: Path, sub: _StubSub, known_type: str):
        self.procedures_root = procedures_root
        self._sub = sub
        self._known_type = known_type
        self.build_uid = None
        self.build_args = None

    def resolve_agent_md(self, subagent_type: str) -> Path:
        # is_file() отражает «известность» типа.
        p = self.procedures_root / "agents" / subagent_type / "agent.md"
        if subagent_type == self._known_type:
            p.parent.mkdir(parents=True, exist_ok=True)
            if not p.exists():
                p.write_text("# stub", encoding="utf-8")
        return p

    def build(self, *, subagent_type, uid, workspace_path, zone):
        self.build_uid = uid
        self.build_args = dict(
            subagent_type=subagent_type, uid=uid, workspace_path=workspace_path, zone=zone
        )
        # Привязать artifacts-зону субагента к зоне, которую передал тул.
        self._sub._artifacts_dir = zone / "artifacts"
        return self._sub


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Собирает session-дерево, монкипатчит runtime/writer/CheckpointManager/фабрику.

    Возвращает SimpleNamespace с ручками для настройки субагента и проверок.
    """
    session_root = tmp_path / "session"
    workspace = session_root / "workspace"
    workspace.mkdir(parents=True)
    checkpoints_root = tmp_path / "checkpoints"
    subagents_root = session_root / "subagents"  # тул выводит её из ctx.workspace_path.parent
    procedures_root = tmp_path / "procedures"    # process-level, вне сессии

    events: list[dict] = []
    order: list[str] = []
    snapshots: list[str] = []

    ctx = SimpleNamespace(
        workspace_path=workspace,
        checkpoints_root=checkpoints_root,
        permission_mode="act",
        decision_mode="confirm",  # НЕ должен попасть к субагенту (хардкод accept_all)
        agent_scope="main",
        subagent_path=None,
        session_id="sess-a",  # субагент наследует
    )
    monkeypatch.setattr(dt, "get_runtime", lambda *a, **k: SimpleNamespace(context=ctx))
    monkeypatch.setattr(dt, "get_stream_writer", lambda *a, **k: events.append)

    class _SpyCM:
        def __init__(self, *, workspace_path, checkpoints_root):
            self.workspace_path = workspace_path
            self.checkpoints_root = checkpoints_root

        def snapshot(self, label):
            order.append("snapshot")
            snapshots.append(label)
            return self.checkpoints_root / label

    monkeypatch.setattr(dt, "CheckpointManager", _SpyCM)

    state = SimpleNamespace(
        session_root=session_root,
        subagents_root=subagents_root,
        events=events,
        order=order,
        snapshots=snapshots,
        order_list=order,
    )

    def install_sub(sub: _StubSub, *, known_type="counter-specialist"):
        sub._order = order
        factory = _StubFactory(procedures_root=procedures_root, sub=sub, known_type=known_type)
        monkeypatch.setattr(dt, "get_subagent_factory", lambda: factory)
        state.factory = factory
        return factory

    state.install_sub = install_sub
    return state


def _make_sub(**kw) -> _StubSub:
    kw.setdefault("events", [])
    kw.setdefault("final_text", "готово")
    return _StubSub(**kw)


# --- метаданные тула ----------------------------------------------------------


def test_tool_metadata():
    """main_only=True (субагент не видит) + is_write=False (не пишет canonical)."""
    assert is_main_only(delegate_to_subagent) is True
    assert is_write_tool(delegate_to_subagent) is False


# --- happy path ---------------------------------------------------------------


async def test_zone_derived_from_runtime_ctx(wired):
    """зона субагента выводится из ctx.workspace_path.parent (корень СВОЕЙ сессии),
    а не из фабрики — процессный холдер сессионных путей не хранит."""
    sub = _make_sub()
    wired.install_sub(sub)

    await delegate_to_subagent.ainvoke({"subagent_type": "counter-specialist", "task_text": "t"})

    uid = wired.factory.build_uid
    assert wired.factory.build_args["zone"] == wired.session_root / "subagents" / uid


async def test_creates_zone(wired):
    sub = _make_sub()
    wired.install_sub(sub)

    await delegate_to_subagent.ainvoke({"subagent_type": "counter-specialist", "task_text": "посчитай"})

    uid = wired.factory.build_uid
    zone = wired.subagents_root / uid
    for subdir in ("notes", "artifacts", ".runtime"):
        assert (zone / subdir).is_dir()


async def test_checkpoint_before_run(wired):
    sub = _make_sub()
    wired.install_sub(sub)

    await delegate_to_subagent.ainvoke({"subagent_type": "counter-specialist", "task_text": "t"})

    # Снимок снят ДО запуска субагента.
    assert wired.order_list.index("snapshot") < wired.order_list.index("run")
    uid6 = wired.factory.build_uid[:6]
    assert wired.snapshots == [f"before_counter-specialist_{uid6}"]


async def test_subagent_run_modes(wired):
    """permission_mode и session_id наследуются, decision_mode хардкод accept_all."""
    sub = _make_sub()
    wired.install_sub(sub)

    await delegate_to_subagent.ainvoke({"subagent_type": "counter-specialist", "task_text": "t"})

    assert sub.ran_with["permission_mode"] == "act"
    assert sub.ran_with["decision_mode"] == "accept_all"
    assert sub.ran_with["session_id"] == "sess-a"  # HITL субагента скоупится сессией main
    assert sub.ran_with["task_text"] == "t"


async def test_multiplexes_events_under_namespace(wired):
    ev1 = StreamEvent(namespace="subagent.x", mode="updates", data={"a": 1})
    ev2 = StreamEvent(namespace="subagent.x", mode="messages", data={"b": 2})
    sub = _make_sub(events=[ev1, ev2])
    wired.install_sub(sub)

    await delegate_to_subagent.ainvoke({"subagent_type": "counter-specialist", "task_text": "t", "task_id": "p1"})

    uid6 = wired.factory.build_uid[:6]
    namespace = f"subagent.counter-specialist.{uid6}"
    types = [e["type"] for e in wired.events]
    assert types == ["namespace_open", "subagent_event", "subagent_event", "namespace_close"]
    # Все события несут namespace субагента и task_id.
    assert all(e["namespace"] == namespace for e in wired.events)
    assert all(e["task_id"] == "p1" for e in wired.events)
    # subagent_event перепаковывает mode/data исходного StreamEvent.
    sub_events = [e for e in wired.events if e["type"] == "subagent_event"]
    assert [e["mode"] for e in sub_events] == ["updates", "messages"]
    assert [e["data"] for e in sub_events] == [{"a": 1}, {"b": 2}]
    # namespace_close без ошибки на happy-path.
    assert wired.events[-1]["error"] is None


async def test_contract_summary_and_artifacts_from_fs(wired):
    """summary = final_text; artifacts = листинг ФС (не self-report)."""
    sub = _make_sub(final_text="посчитал до 10", write_file="count.md")
    wired.install_sub(sub)

    result = await delegate_to_subagent.ainvoke(
        {"subagent_type": "counter-specialist", "task_text": "посчитай и запиши"}
    )

    uid = wired.factory.build_uid
    assert result["summary"] == "посчитал до 10"
    assert result["artifacts"] == [f"subagents/{uid}/artifacts/count.md"]


async def test_empty_artifacts_when_nothing_written(wired):
    sub = _make_sub(final_text="ничего не писал")
    wired.install_sub(sub)

    result = await delegate_to_subagent.ainvoke({"subagent_type": "counter-specialist", "task_text": "t"})

    assert result["artifacts"] == []


# --- краш-изоляция ------------------------------------------------------------


async def test_subagent_crash_isolated(wired):
    """Исключение субагента → штатный failed tool_result, не проброс из тула."""
    sub = _make_sub(raises=RuntimeError("boom"))
    wired.install_sub(sub)

    result = await delegate_to_subagent.ainvoke({"subagent_type": "counter-specialist", "task_text": "t"})

    assert result["artifacts"] == []
    assert result["error"] == "boom"
    assert "ошибк" in result["summary"].lower()
    # namespace_close эмитнут с пометкой ошибки (finally).
    assert wired.events[-1]["type"] == "namespace_close"
    assert wired.events[-1]["error"] == "boom"


async def test_crash_after_checkpoint(wired):
    """Чекпоинт ПЕРЕД снят даже если субагент падает (снимок «до»)."""
    sub = _make_sub(raises=RuntimeError("boom"))
    wired.install_sub(sub)

    await delegate_to_subagent.ainvoke({"subagent_type": "counter-specialist", "task_text": "t"})

    assert len(wired.snapshots) == 1


# --- неизвестный тип ----------------------------------------------------------


async def test_unknown_type_raises_delegation_error(wired):
    """Нет agent.md → DelegationError (self-correctable), до создания зоны/чекпоинта."""
    sub = _make_sub()
    wired.install_sub(sub, known_type="counter-specialist")

    with pytest.raises(DelegationError, match="неизвестный тип"):
        await delegate_to_subagent.ainvoke({"subagent_type": "nope", "task_text": "t"})

    # Чекпоинта нет — отказ случился до шага снимка.
    assert wired.snapshots == []
