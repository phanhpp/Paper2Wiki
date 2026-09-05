"""`any2wiki keys` — never leak a secret, never lose an existing one.

Three properties this has to hold, and each has a way of quietly failing:

* **Masking must not reveal.** `first6 + last4` on a short key exposes almost all of it.
* **`.env` must be `0o600`.** `set_key` creates at umask default — often `644`,
  world-readable — and never chmods.
* **Writing one key must not drop the others.** A naive open/write would.
"""

from __future__ import annotations

import os

import pytest
from typer.testing import CliRunner

import src.cli.app as appmod
import src.paths as paths
from src.cli.app import app
from src.cli.commands.keys import mask

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(appmod, "load_env", lambda *a, **k: None)
    monkeypatch.setenv(paths._HOME_ENV, str(tmp_path))
    yield


# --- masking ---------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("secret", [
    "FAKE-KEY-abcdefghijklmnop-1234",
    "sk-proj-XXXXXXXXXXXXXXXXXXXXXXXX",
])
def test_mask_hides_the_middle(secret):
    out = mask(secret)
    assert secret not in out
    assert secret[8:-6] not in out, "the middle must never appear"
    assert out.startswith(secret[:6]) and out.endswith(secret[-4:])


@pytest.mark.unit
@pytest.mark.parametrize("short", ["sk-123", "abcdefghijkl", "x", "a" * 14])
def test_mask_reveals_nothing_from_a_short_secret(short):
    """first6+last4 on a 12-char key would show 10 of 12 — so short values show nothing."""
    assert mask(short) == "…"
    assert short not in mask(short)


# --- keys list -------------------------------------------------------------------

@pytest.mark.unit
def test_list_never_prints_a_raw_key(monkeypatch):
    secret = "FAKE-KEY-SUPERSECRETVALUE-9999"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)

    result = runner.invoke(app, ["keys", "list"])
    assert result.exit_code == 0
    assert secret not in result.output
    assert "SUPERSECRET" not in result.output


@pytest.mark.unit
def test_list_distinguishes_set_from_unset(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "FAKE-KEY-something-long-enough")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    flat = " ".join(runner.invoke(app, ["keys", "list"]).output.split())
    assert "ANTHROPIC_API_KEY" in flat and "GROQ_API_KEY" in flat
    assert "not set" in flat


@pytest.mark.unit
def test_list_flags_the_key_the_configured_model_needs(monkeypatch):
    """The useful part: not just what is set, but what *this* config requires."""
    monkeypatch.setenv("ANY2WIKI_MODEL", "openai:gpt-4o")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    flat = " ".join(runner.invoke(app, ["keys", "list"]).output.split())
    assert "OPENAI_API_KEY is not set" in flat
    assert "keys set OPENAI_API_KEY" in flat, "should say how to fix it"


@pytest.mark.unit
def test_list_reads_the_environment_not_the_file(monkeypatch):
    """A key exported in the shell is what a run uses, even if .env lacks it."""
    paths.ensure_user_root()
    paths.env_path().write_text("")                       # empty file
    monkeypatch.setenv("ANTHROPIC_API_KEY", "FAKE-KEY-exported-in-the-shell")

    flat = " ".join(runner.invoke(app, ["keys", "list"]).output.split())
    assert "FAKE-K" in flat, "an exported key must show as set"


# --- keys set --------------------------------------------------------------------

@pytest.mark.unit
def test_set_writes_the_key_and_reports_it_masked():
    secret = "sk-test-abcdefghijklmnop"
    result = runner.invoke(app, ["keys", "set", "SOME_API_KEY", secret])

    assert result.exit_code == 0
    assert secret in paths.env_path().read_text()
    assert secret not in result.output, "the confirmation must not echo the key"


@pytest.mark.unit
def test_set_creates_env_with_owner_only_permissions():
    """set_key creates at umask default (often 644) and never chmods."""
    runner.invoke(app, ["keys", "set", "SOME_API_KEY", "sk-test-abcdefghijklmnop"])

    mode = paths.env_path().stat().st_mode & 0o777
    assert mode == 0o600, f".env is {oct(mode)} — a secret must not be group/world readable"


@pytest.mark.unit
def test_set_tightens_a_pre_existing_world_readable_env():
    paths.ensure_user_root()
    paths.env_path().write_text("OLD_KEY=value\n")
    paths.env_path().chmod(0o644)

    runner.invoke(app, ["keys", "set", "NEW_API_KEY", "sk-test-abcdefghijklmnop"])
    assert paths.env_path().stat().st_mode & 0o777 == 0o600


@pytest.mark.unit
def test_set_preserves_other_keys():
    paths.ensure_user_root()
    paths.env_path().write_text("KEEP_ME=untouched\nALSO_KEEP=yes\n")

    runner.invoke(app, ["keys", "set", "NEW_API_KEY", "sk-test-abcdefghijklmnop"])

    text = paths.env_path().read_text()
    assert "KEEP_ME=untouched" in text
    assert "ALSO_KEEP=yes" in text
    assert "NEW_API_KEY" in text


@pytest.mark.unit
def test_set_replaces_rather_than_appends_a_duplicate():
    runner.invoke(app, ["keys", "set", "SOME_API_KEY", "first-value-aaaaaaaa"])
    runner.invoke(app, ["keys", "set", "SOME_API_KEY", "second-value-bbbbbbb"])

    text = paths.env_path().read_text()
    assert text.count("SOME_API_KEY") == 1, "a second write must update, not duplicate"
    assert "second-value" in text and "first-value" not in text


@pytest.mark.unit
def test_set_uppercases_the_name():
    runner.invoke(app, ["keys", "set", "some_api_key", "sk-test-abcdefghijklmnop"])
    assert "SOME_API_KEY" in paths.env_path().read_text()


@pytest.mark.unit
def test_set_prompts_when_no_value_is_given():
    """Prompting is the default so a key never lands in shell history."""
    result = runner.invoke(app, ["keys", "set", "SOME_API_KEY"], input="sk-typed-secretly\n")

    assert result.exit_code == 0
    assert "sk-typed-secretly" in paths.env_path().read_text()


@pytest.mark.unit
def test_set_with_an_empty_value_changes_nothing():
    """Enter on the prompt means cancel — not 'clear the key'."""
    result = runner.invoke(app, ["keys", "set", "SOME_API_KEY"], input="\n")

    assert result.exit_code == 1
    assert not paths.env_path().exists() or "SOME_API_KEY" not in paths.env_path().read_text()


@pytest.mark.unit
def test_set_creates_a_missing_user_root(tmp_path, monkeypatch):
    """A fresh install has no ~/.any2wiki — writing a key must not crash."""
    target = tmp_path / "brand" / "new"
    monkeypatch.setenv(paths._HOME_ENV, str(target))

    result = runner.invoke(app, ["keys", "set", "SOME_API_KEY", "sk-test-abcdefghijklmnop"])
    assert result.exit_code == 0
    assert (target / ".env").exists()


@pytest.mark.unit
def test_set_never_writes_to_config_yaml():
    """Secrets belong in .env. config.yaml is committed-adjacent and must stay clean."""
    runner.invoke(app, ["keys", "set", "SOME_API_KEY", "sk-test-abcdefghijklmnop"])

    assert not paths.config_path().exists() or \
        "sk-test" not in paths.config_path().read_text()
