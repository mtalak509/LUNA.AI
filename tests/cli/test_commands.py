"""CLI `/attach` — the console counterpart of the HTTP upload.

It must agree with the route on everything below the transport (path policy, size cap,
checkpoint), which it gets by going through the same `WorkspaceManager`. What is tested
here is what only the CLI owns: argument parsing and the printed outcome.
"""

from __future__ import annotations

import pytest

from cli import commands


@pytest.fixture
def cli_zone(tmp_path, monkeypatch):
    """Point the CLI at a throwaway session. Returns (workspace, checkpoints)."""
    session_dir = tmp_path / "session"
    (session_dir / "workspace").mkdir(parents=True)
    monkeypatch.setattr(commands, "SESSION_DIR", session_dir)
    return session_dir / "workspace", session_dir / "checkpoints"


def test_attach_copies_file_and_snapshots(cli_zone, tmp_path, capsys):
    workspace, checkpoints = cli_zone
    source = tmp_path / "report.json"
    source.write_bytes(b'{"a": 1}')

    commands._attach(str(source))

    out = capsys.readouterr().out
    assert (workspace / "report.json").read_bytes() == b'{"a": 1}'
    assert "загружено: report.json" in out
    # Same guarantee as the route: /undo after an attach must not swallow the file.
    (snapshot,) = list(checkpoints.iterdir())
    assert (snapshot / "report.json").is_file()
    assert snapshot.name in out


def test_attach_handles_paths_with_spaces_and_quotes(cli_zone, tmp_path, capsys):
    """Windows paths contain spaces; the REPL takes the rest of the line as one path."""
    workspace, _ = cli_zone
    source = tmp_path / "my big report.json"
    source.write_bytes(b"{}")

    commands._attach(f'"{source}"')

    assert (workspace / "my big report.json").is_file()
    assert "загружено: my big report.json" in capsys.readouterr().out


def test_attach_refuses_duplicate_until_overwrite(cli_zone, tmp_path, capsys):
    workspace, _ = cli_zone
    source = tmp_path / "report.json"
    source.write_bytes(b"first")
    commands._attach(str(source))

    source.write_bytes(b"second")
    commands._attach(str(source))
    assert "ошибка" in capsys.readouterr().out
    assert (workspace / "report.json").read_bytes() == b"first"

    commands._attach(f"{source} --overwrite")
    assert (workspace / "report.json").read_bytes() == b"second"


def test_attach_overwrite_flag_may_lead(cli_zone, tmp_path):
    """The flag is matched as a token, so its position on the line does not matter."""
    workspace, _ = cli_zone
    source = tmp_path / "report.json"
    source.write_bytes(b"first")
    commands._attach(str(source))

    source.write_bytes(b"second")
    commands._attach(f"--overwrite {source}")

    assert (workspace / "report.json").read_bytes() == b"second"


def test_attach_without_argument_prints_usage(cli_zone, capsys):
    workspace, _ = cli_zone
    commands._attach("   ")
    assert "использование: /attach" in capsys.readouterr().out
    assert list(workspace.rglob("*")) == []


def test_attach_missing_source_reports_error(cli_zone, tmp_path, capsys):
    workspace, _ = cli_zone
    commands._attach(str(tmp_path / "nope.json"))
    assert "ошибка" in capsys.readouterr().out
    assert list(workspace.rglob("*")) == []


def test_attach_is_dispatched_by_handle_command(cli_zone, tmp_path, capsys):
    workspace, _ = cli_zone
    source = tmp_path / "report.json"
    source.write_bytes(b"{}")

    assert commands.handle_command(f"/attach {source}") is True

    assert (workspace / "report.json").is_file()
    assert "загружено" in capsys.readouterr().out


def test_help_lists_attach(capsys):
    commands.handle_command("/help")
    assert "/attach" in capsys.readouterr().out
