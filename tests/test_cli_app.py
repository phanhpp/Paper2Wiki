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
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for cmd in ("repl", "chat", "sessions", "config"):
        assert cmd in result.output


@pytest.mark.unit
def test_no_args_shows_help():
    # no_args_is_help=True → prints usage and exits 2 (Click treats "no command" as usage error).
    result = runner.invoke(app, [])
    assert result.exit_code == 2 # wrong parameters
    assert "Usage" in result.output


@pytest.mark.unit
def test_config_show_runs_offline():
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0
    assert "Ingest mode" in result.output


@pytest.mark.unit
def test_config_show_ingest_mode_flag_is_wired():
    # The --ingest-mode flag must flow through apply_env into the resolved config.
    result = runner.invoke(app, ["config", "show", "--ingest-mode", "quality"])
    assert result.exit_code == 0
    assert "quality" in result.output


@pytest.mark.unit
def test_sessions_ls_smoke():
    # Read-only against the real sessions DB; exit 0 whether empty or populated.
    result = runner.invoke(app, ["sessions", "ls", "-n", "1"])
    assert result.exit_code == 0


@pytest.mark.unit
def test_chat_missing_anthropic_key_exits_1(monkeypatch):
    # Stub load_dotenv so the callback can't repopulate the key from .env, then remove it.
    import src.cli.app as appmod

    monkeypatch.setattr(appmod, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = runner.invoke(app, ["chat", "hi", "--eval-mode"])
    assert result.exit_code == 1
    assert "ANTHROPIC_API_KEY" in result.output


@pytest.mark.unit
def test_invalid_ingest_mode_value_rejected():
    # Enum-typed option → Typer rejects unknown values with a non-zero exit.
    result = runner.invoke(app, ["config", "show", "--ingest-mode", "bogus"])
    assert result.exit_code != 0
