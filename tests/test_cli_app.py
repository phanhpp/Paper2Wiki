"""End-to-end CLI wiring tests via Typer's CliRunner.

These complement ``test_cli.py`` (which tests helper logic in isolation): here we invoke the
actual Typer app so arg parsing, command registration, exit codes, and fail-fast paths are
exercised the way a user hits them. We deliberately do NOT drive ``chat``/``repl`` to
completion — those build the supervisor and call the LLM, so they belong to manual/eval
verification, not unit tests.
"""
from __future__ import annotations

import pytest
from typer.testing import CliRunner

from src.cli.app import app

runner = CliRunner()


@pytest.mark.unit
def test_help_lists_all_commands():
    """`--help` registers and lists all four top-level commands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("repl", "chat", "sessions", "config"):
        assert cmd in result.output


@pytest.mark.unit
def test_no_args_shows_help():
    """With no command, no_args_is_help prints usage and exits 2 (Click usage-error convention)."""
    result = runner.invoke(app, [])
    assert result.exit_code == 2
    assert "Usage" in result.output


@pytest.mark.unit
def test_config_show_runs_offline():
    """`config show` runs without network/agent and prints the resolved config."""
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "Ingest mode" in result.output


@pytest.mark.unit
def test_config_show_ingest_mode_flag_is_wired():
    """`--ingest-mode quality` is parsed and reflected in the resolved config output."""
    result = runner.invoke(app, ["config", "show", "--ingest-mode", "quality"])
    assert result.exit_code == 0
    assert "quality" in result.output


@pytest.mark.unit
def test_sessions_ls_smoke():
    """`sessions ls` is read-only and exits 0 whether the DB is empty or populated."""
    result = runner.invoke(app, ["sessions", "ls", "-n", "1"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_chat_missing_anthropic_key_exits_1(monkeypatch):
    """`chat` fails fast (exit 1) when ANTHROPIC_API_KEY is absent, before building the agent.

    load_env is stubbed so the callback can't repopulate the key from .env.
    """
    import src.cli.app as appmod

    monkeypatch.setattr(appmod, "load_env", lambda *a, **k: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = runner.invoke(app, ["chat", "hi", "--eval-mode"])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output


@pytest.mark.unit
def test_resume_unknown_ref_exits_1():
    """`resume` with an id/title that resolves to nothing exits 1 without building the agent."""
    result = runner.invoke(app, ["sessions", "resume", "definitely-not-a-real-session-xyz"])
    assert result.exit_code == 1
    assert "No session matching" in result.output


@pytest.mark.unit
def test_rename_unknown_ref_exits_1():
    """`rename` with an unresolvable id/title exits 1 with a clear message."""
    result = runner.invoke(app, ["sessions", "rename", "no-such-session-xyz", "whatever"])
    assert result.exit_code == 1
    assert "No session matching" in result.output


@pytest.mark.unit
def test_invalid_ingest_mode_value_rejected():
    """An unknown --ingest-mode value is rejected by the Enum (non-zero exit)."""
    result = runner.invoke(app, ["config", "show", "--ingest-mode", "bogus"])
    assert result.exit_code != 0
