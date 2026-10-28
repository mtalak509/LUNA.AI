"""CLI input: completion of slash commands, their arguments and `@` workspace paths."""

from __future__ import annotations

from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from cli import commands, prompt
from cli.commands import COMMANDS
from cli.config import state


def _complete(completer: prompt.LunaCompleter, text: str) -> list[str]:
    return [c.text for c in completer.get_completions(Document(text), CompleteEvent())]


def test_slash_completes_every_command(tmp_path):
    completer = prompt.LunaCompleter(tmp_path)
    assert _complete(completer, "/") == list(COMMANDS)
    assert _complete(completer, "/c") == ["/confirm", "/cp"]


def test_at_completes_workspace_paths(tmp_path):
    (tmp_path / "attachments").mkdir()
    (tmp_path / "attachments" / "report.json").write_text("{}")
    (tmp_path / "notes.md").write_text("")
    (tmp_path / ".runtime").mkdir()
    (tmp_path / ".runtime" / "secret").write_text("")
    completer = prompt.LunaCompleter(tmp_path)

    assert _complete(completer, "сравни @rep") == ["@attachments/report.json"]
    assert "@notes.md" in _complete(completer, "@")
    assert not any(".runtime" in c for c in _complete(completer, "@"))
    assert _complete(completer, "без собаки rep") == []


def test_fs_completes_directories_only(tmp_path):
    (tmp_path / "attachments").mkdir()
    (tmp_path / "notes.md").write_text("")
    assert _complete(prompt.LunaCompleter(tmp_path), "/fs ") == ["attachments/"]


def test_undo_completes_checkpoint_ids(tmp_path, monkeypatch):
    session_dir = tmp_path / "session"
    (session_dir / "checkpoints" / "c001_first").mkdir(parents=True)
    (session_dir / "workspace").mkdir()
    monkeypatch.setattr(commands, "SESSION_DIR", session_dir)
    assert _complete(prompt.LunaCompleter(session_dir / "workspace"), "/undo c0") == ["c001_first"]


def test_shift_tab_toggles_permission_mode(monkeypatch):
    monkeypatch.setitem(state, "perm", "act")
    prompt.toggle_permission_mode()
    assert state["perm"] == "plan"
    prompt.toggle_permission_mode()
    assert state["perm"] == "act"


def test_help_lists_every_command(capsys):
    commands.handle_command("/help")
    out = capsys.readouterr().out
    for name in COMMANDS:
        assert name in out
