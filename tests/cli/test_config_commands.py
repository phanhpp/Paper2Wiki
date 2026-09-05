"""`any2wiki config` — path resolution and the precedence a run actually used.

The bug these guard against is silent: you edit `auxiliary.judge.model`, `config show`
prints the old value, and nothing explains that an env var outranks the file. Both the
"From" column and `config path` exist so the cause is visible next to the symptom.

No network, no LLM — config is injected and the CLI is driven through CliRunner.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

import src.cli.app as appmod
import src.paths as paths
from src.cli.app import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """No real .env, no inherited model vars, a private user_root."""
    monkeypatch.setattr(appmod, "load_env", lambda *a, **k: None)
    monkeypatch.setenv(paths._HOME_ENV, str(tmp_path))
    for var in list(__import__("os").environ):
        if var.startswith("ANY2WIKI_") and var != paths._HOME_ENV:
            monkeypatch.delenv(var, raising=False)
    yield


def _write_config(text: str) -> None:
    paths.ensure_user_root()
    paths.config_path().write_text(text)


def _flat(result) -> str:
    """Table output wraps; compare on collapsed whitespace."""
    return " ".join(result.output.split())


# --- config path ----------------------------------------------------------------

@pytest.mark.unit
def test_path_prints_the_file_in_use():
    _write_config("model:\n  default: x\n")
    result = runner.invoke(app, ["config", "path"])

    assert result.exit_code == 0
    assert str(paths.config_path()) in result.output


@pytest.mark.unit
def test_path_says_where_setup_would_write_when_none_exists():
    """A missing config must be a signpost, not a silent default."""
    result = runner.invoke(app, ["config", "path"])

    assert result.exit_code == 1, "no config is a failure state worth an exit code"
    flat = _flat(result)
    assert "No config file found" in flat
    assert str(paths.config_path()) in flat


@pytest.mark.unit
def test_path_follows_the_env_override(tmp_path, monkeypatch):
    """ANY2WIKI_CONFIG wins over user_root(), and `path` must say so."""
    elsewhere = tmp_path / "other.yaml"
    elsewhere.write_text("model:\n  default: y\n")
    monkeypatch.setenv("ANY2WIKI_CONFIG", str(elsewhere))

    result = runner.invoke(app, ["config", "path"])
    assert str(elsewhere) in result.output


# --- config show: the file row ---------------------------------------------------

@pytest.mark.unit
def test_show_names_the_config_file_it_read():
    _write_config("model:\n  default: from-the-file\n")
    flat = _flat(runner.invoke(app, ["config", "show"]))

    assert "Config file" in flat
    assert "from-the-file" in flat


@pytest.mark.unit
def test_show_flags_a_missing_config_rather_than_looking_normal():
    flat = _flat(runner.invoke(app, ["config", "show"]))
    assert "none found" in flat and "any2wiki setup" in flat


# --- config show: the From column ------------------------------------------------

@pytest.mark.unit
def test_from_column_names_the_task_block_when_it_wins():
    _write_config(
        "model:\n  default: base-model\n"
        "auxiliary:\n  judge:\n    model: pinned-model\n"
    )
    flat = _flat(runner.invoke(app, ["config", "show"]))
    assert "auxiliary.judge.model" in flat


@pytest.mark.unit
def test_from_column_names_model_default_for_unpinned_tasks():
    _write_config("model:\n  default: base-model\n")
    flat = _flat(runner.invoke(app, ["config", "show"]))
    assert "model.default" in flat


@pytest.mark.unit
def test_from_column_names_the_task_env_var_when_it_shadows_the_file(monkeypatch):
    """The regression this column exists for: level 1 beats a config edit, silently."""
    _write_config(
        "model:\n  default: base-model\n"
        "auxiliary:\n  judge:\n    model: pinned-in-file\n"
    )
    monkeypatch.setenv("ANY2WIKI_MODEL_JUDGE", "from-the-env")

    flat = _flat(runner.invoke(app, ["config", "show"]))
    assert "from-the-env" in flat, "the env var should win"
    assert "ANY2WIKI_MODEL_JUDGE" in flat, "and the table must say why"


@pytest.mark.unit
def test_from_column_names_the_global_env_var(monkeypatch):
    _write_config("model:\n  default: base-model\n")
    monkeypatch.setenv("ANY2WIKI_MODEL", "global-env-model")

    flat = _flat(runner.invoke(app, ["config", "show"]))
    assert "global-env-model" in flat
    assert "ANY2WIKI_MODEL" in flat


@pytest.mark.unit
def test_from_column_falls_back_to_built_in_with_no_config():
    """Nothing configured anywhere — the source is the hardcoded fallback."""
    flat = _flat(runner.invoke(app, ["config", "show"]))
    assert "built-in" in flat


@pytest.mark.unit
def test_task_env_var_outranks_global_env_var(monkeypatch):
    """Level 1 over level 3 — the From column must name the winner, not either one."""
    _write_config("model:\n  default: base\n")
    monkeypatch.setenv("ANY2WIKI_MODEL", "global")
    monkeypatch.setenv("ANY2WIKI_MODEL_JUDGE", "task-specific")

    flat = _flat(runner.invoke(app, ["config", "show"]))
    assert "task-specific" in flat
    assert "ANY2WIKI_MODEL_JUDGE" in flat


# --- config show: routing --------------------------------------------------------

@pytest.mark.unit
def test_endpoint_is_a_dash_for_the_providers_own_api():
    _write_config("model:\n  default: claude-sonnet-4-6\n")
    flat = _flat(runner.invoke(app, ["config", "show"]))
    assert "anthropic" in flat


@pytest.mark.unit
def test_endpoint_shows_a_custom_base_url():
    """The column exists so a Claude model going to OpenRouter is visible."""
    _write_config(
        "model:\n"
        "  default: anthropic/claude-sonnet-4.5\n"
        "  provider: openai\n"
        "  base_url: https://openrouter.ai/api/v1\n"
    )
    flat = _flat(runner.invoke(app, ["config", "show"]))
    assert "openrouter.ai" in flat


@pytest.mark.unit
def test_model_flag_is_reflected_without_touching_the_file():
    """`config show -m` previews an override; the file must be unchanged after."""
    _write_config("model:\n  default: from-the-file\n")
    before = paths.config_path().read_text()

    flat = _flat(runner.invoke(app, ["config", "show", "-m", "openai:gpt-4o"]))

    assert "openai:gpt-4o" in flat
    assert paths.config_path().read_text() == before, "show must never write"


# --- config set ------------------------------------------------------------------

def _read_yaml():
    import yaml
    return yaml.safe_load(paths.config_path().read_text())


@pytest.mark.unit
def test_set_writes_a_nested_key_and_shows_the_result():
    _write_config("model:\n  default: base\n")
    result = runner.invoke(app, ["config", "set", "auxiliary.judge.model", "gpt-4o"])

    assert result.exit_code == 0
    assert _read_yaml()["auxiliary"]["judge"]["model"] == "gpt-4o"
    assert "gpt-4o" in _flat(result), "should render the outcome, not just claim success"


@pytest.mark.unit
def test_set_creates_config_when_none_exists():
    assert not paths.config_path().exists()
    result = runner.invoke(app, ["config", "set", "model.default", "openai:gpt-4o"])

    assert result.exit_code == 0
    assert _read_yaml()["model"]["default"] == "openai:gpt-4o"


@pytest.mark.unit
def test_set_preserves_unrelated_keys():
    _write_config("model:\n  default: base\nweb:\n  backend: firecrawl\n")
    runner.invoke(app, ["config", "set", "auxiliary.title.model", "haiku"])

    data = _read_yaml()
    assert data["web"]["backend"] == "firecrawl", "an unrelated block must survive"
    assert data["model"]["default"] == "base"


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    ("60", 60), ("0.5", 0.5), ("true", True), ("false", False), ("gpt-4o", "gpt-4o"),
])
def test_set_coerces_types(raw, expected):
    """`timeout: "60"` and `timeout: 60` are different values to a client library."""
    _write_config("model:\n  default: base\n")
    runner.invoke(app, ["config", "set", "auxiliary.judge.timeout", raw])

    got = _read_yaml()["auxiliary"]["judge"]["timeout"]
    assert got == expected and type(got) is type(expected)


@pytest.mark.unit
@pytest.mark.parametrize("key", [
    "model.api_key",                 # the one a whole-string suffix check misses
    "auxiliary.judge.api_key",
    "model.access_token",
    "web.client_secret",
])
def test_set_refuses_a_secret_and_points_at_keys_set(key):
    """Secrets belong in .env. config.yaml is committed-adjacent."""
    _write_config("model:\n  default: base\n")
    result = runner.invoke(app, ["config", "set", key, "sk-should-not-land-here"])

    assert result.exit_code == 1
    assert "keys set" in _flat(result)
    assert "sk-should-not-land-here" not in paths.config_path().read_text()


@pytest.mark.unit
def test_set_refuses_an_unknown_task():
    """A typo would otherwise create a key nothing ever reads."""
    _write_config("model:\n  default: base\n")
    result = runner.invoke(app, ["config", "set", "auxiliary.judeg.model", "x"])

    assert result.exit_code == 1
    assert "judeg" not in paths.config_path().read_text()
    assert "Unknown task" in _flat(result)


@pytest.mark.unit
def test_set_refuses_an_unknown_top_level_block():
    _write_config("model:\n  default: base\n")
    result = runner.invoke(app, ["config", "set", "nonsense.key", "v"])

    assert result.exit_code == 1
    assert "nonsense" not in paths.config_path().read_text()


@pytest.mark.unit
def test_set_replaces_a_scalar_standing_where_a_block_is_needed():
    """`model: "x"` then setting `model.default` must not crash on a non-dict."""
    _write_config("model: not-a-mapping\n")
    result = runner.invoke(app, ["config", "set", "model.default", "gpt-4o"])

    assert result.exit_code == 0
    assert _read_yaml()["model"]["default"] == "gpt-4o"


@pytest.mark.unit
def test_set_survives_an_empty_config_file():
    paths.ensure_user_root()
    paths.config_path().write_text("")
    result = runner.invoke(app, ["config", "set", "model.default", "gpt-4o"])

    assert result.exit_code == 0
    assert _read_yaml()["model"]["default"] == "gpt-4o"


@pytest.mark.unit
def test_set_is_visible_to_the_resolver_immediately():
    """The point of the command: a run must pick the value up."""
    _write_config("model:\n  default: base\n")
    runner.invoke(app, ["config", "set", "auxiliary.judge.model", "openai:gpt-4o-mini"])

    from src.llm_roles import get_model_spec
    assert get_model_spec("judge").model == "openai:gpt-4o-mini"


@pytest.mark.unit
def test_path_output_is_a_single_pipeable_line():
    """`$(any2wiki config path)` must work — Rich would wrap a long path across lines."""
    _write_config("model:\n  default: x\n")
    result = runner.invoke(app, ["config", "path"])

    lines = [l for l in result.output.splitlines() if l.strip()]
    assert len(lines) == 1
    assert lines[0].strip() == str(paths.config_path())


@pytest.mark.unit
def test_show_names_shadowing_vars_in_full_below_the_table(monkeypatch):
    """The From column can only fit "env" — the footer must name the variable."""
    _write_config("model:\n  default: base\n")
    monkeypatch.setenv("ANY2WIKI_MODEL_JUDGE", "shadowed")

    flat = _flat(runner.invoke(app, ["config", "show"]))
    assert "Overridden by environment variables" in flat
    assert "ANY2WIKI_MODEL_JUDGE" in flat


@pytest.mark.unit
def test_show_has_no_override_footer_when_nothing_is_shadowing():
    _write_config("model:\n  default: base\n")
    flat = _flat(runner.invoke(app, ["config", "show"]))
    assert "Overridden by environment" not in flat
