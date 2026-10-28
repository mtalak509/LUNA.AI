"""
Unit tests for tool output truncation: the pure cut (OutputTruncator) and the decorator.

Edge cases covered by name: output within limits is untouched; the line limit; the byte limit;
one line longer than the byte limit is cut on a character boundary (Cyrillic); `tail` keeps the
end and puts the note first; spill goes to the calling agent's zone (main / subagent);
a failed spill write degrades to a note; tool errors pass through. `cut_window` (read): the
header and note name the lines actually shown and the next offset.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.agent.tools import tool_output as to
from core.agent.tools.fs_paths import SPILL_DIR, FileAccessError
from core.agent.tools.tool_output import OutputTruncator, cut_window, truncate_output


def _lines(n: int, width: int = 3) -> str:
    return "".join(f"{i:0{width}d}\n" for i in range(1, n + 1))


@pytest.fixture()
def zones(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Main runtime context patched into tool_output; switch to a subagent via `.as_subagent()`."""
    workspace = tmp_path / "session" / "workspace"
    subagent = tmp_path / "session" / "subagents" / "uid"
    workspace.mkdir(parents=True)
    subagent.mkdir(parents=True)
    ctx = SimpleNamespace(agent_scope="main", workspace_path=workspace, subagent_path=None)
    monkeypatch.setattr(to, "get_runtime", lambda *a, **k: SimpleNamespace(context=ctx))

    def as_subagent() -> None:
        ctx.agent_scope, ctx.subagent_path = "subagent", subagent

    return SimpleNamespace(workspace=workspace, subagent=subagent, as_subagent=as_subagent)


# --- OutputTruncator: the pure cut ---------------------------------------------------


def test_within_limits_is_not_cut() -> None:
    assert OutputTruncator("head", max_lines=5, max_bytes=100).cut(_lines(5)) is None


def test_head_line_limit_keeps_first_lines() -> None:
    cut = OutputTruncator("head", max_lines=3, max_bytes=1000).cut(_lines(10))
    assert cut is not None
    assert cut.text == "001\n002\n003\n"
    assert (cut.shown_lines, cut.total_lines) == (3, 10)


def test_head_byte_limit_cuts_on_line_boundary() -> None:
    # Each line is 4 bytes; 10 bytes fit two whole lines, not two and a half.
    cut = OutputTruncator("head", max_lines=100, max_bytes=10).cut(_lines(10))
    assert cut is not None
    assert cut.text == "001\n002\n"
    assert (cut.shown_bytes, cut.total_bytes) == (8, 40)


def test_head_single_huge_line_is_cut_on_character_boundary() -> None:
    # "ж" is 2 bytes in UTF-8; an odd byte limit must not split it.
    cut = OutputTruncator("head", max_lines=100, max_bytes=7).cut("ж" * 50)
    assert cut is not None
    assert cut.text == "жжж"
    assert cut.shown_bytes == 6


def test_tail_keeps_last_lines() -> None:
    cut = OutputTruncator("tail", max_lines=2, max_bytes=1000).cut(_lines(10))
    assert cut is not None
    assert cut.text == "009\n010\n"


def test_tail_single_huge_line_keeps_its_end() -> None:
    cut = OutputTruncator("tail", max_lines=100, max_bytes=7).cut("a" + "ж" * 50)
    assert cut is not None
    assert cut.text == "жжж"


def test_unknown_strategy_rejected() -> None:
    with pytest.raises(ValueError):
        OutputTruncator("middle")  # type: ignore[arg-type]


# --- cut_window: read's own cut ------------------------------------------------------


def test_window_within_limits_is_not_cut() -> None:
    assert cut_window(_lines(10), 1, 10) is None


def test_window_cut_names_shown_lines_and_next_offset() -> None:
    result = cut_window(_lines(3000, width=4), 1, 3000)
    assert result is not None
    lines = result.splitlines()
    assert lines[0] == "[строки 1–2000 из 3000]"
    assert lines[1] == "0001" and lines[-2] == "2000"
    assert lines[-1] == "[вывод обрезан: показаны строки 1–2000 из 3000. Продолжай с offset=2001.]"


def test_window_cut_counts_from_its_start() -> None:
    result = cut_window(_lines(2500, width=4), 501, 3000)
    assert result is not None
    assert result.splitlines()[0] == "[строки 501–2500 из 3000]"
    assert result.endswith("Продолжай с offset=2501.]")


# --- @truncate_output: note and spill ------------------------------------------------


async def test_decorator_leaves_small_output_untouched(zones) -> None:
    @truncate_output("head")
    async def grep() -> str:
        return "a:1: x"

    assert await grep() == "a:1: x"
    assert not (zones.workspace / SPILL_DIR).exists()


async def test_head_spill_note_points_to_full_output_in_workspace(zones) -> None:
    full = _lines(3000, width=4)

    @truncate_output("head")
    async def grep() -> str:
        return full

    result = await grep()
    assert result.startswith("0001\n")
    note = result.splitlines()[-1]
    assert note.startswith("[вывод обрезан: показано 2000 из 3000 строк")
    spill_rel = next(word for word in note.split() if word.startswith(SPILL_DIR))
    assert spill_rel.startswith(f"{SPILL_DIR}/grep-")
    assert (zones.workspace / spill_rel).read_text(encoding="utf-8") == full


async def test_subagent_spill_goes_to_its_own_zone(zones) -> None:
    zones.as_subagent()

    @truncate_output("head")
    async def find() -> str:
        return _lines(3000)

    await find()
    assert len(list((zones.subagent / SPILL_DIR).iterdir())) == 1
    assert not (zones.workspace / SPILL_DIR).exists()


async def test_tail_puts_note_first_and_keeps_end(zones) -> None:
    @truncate_output("tail")
    async def shell() -> str:
        return _lines(3000, width=4)

    result = await shell()
    assert result.startswith("[вывод обрезан")
    assert result.endswith("3000\n")


async def test_failed_spill_write_still_returns_cut_output(zones) -> None:
    (zones.workspace / SPILL_DIR).write_text("not a directory", encoding="utf-8")

    @truncate_output("head")
    async def grep() -> str:
        return _lines(3000, width=4)

    result = await grep()
    assert result.startswith("0001\n")
    assert "сохранить не удалось" in result.splitlines()[-1]


async def test_tool_error_passes_through(zones) -> None:
    @truncate_output("head")
    async def grep() -> str:
        raise FileAccessError("не найдено: 'x'")

    with pytest.raises(FileAccessError, match="не найдено"):
        await grep()
