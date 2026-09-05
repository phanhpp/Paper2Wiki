"""`any2wiki setup` — the wizard must produce a config a run can actually resolve.

Two behaviours worth pinning:

* **A missing API key warns; it does not fail.** Writing config now and adding the key
  later is normal, and `require_keys()` already refuses at run time naming the variable.
  Failing here would block a valid flow and duplicate that check.
* **It must refuse to clobber.** An existing config holds someone's choices.
"""

from __future__ import annotations

import pytest
import yaml
from typer.testing import CliRunner

import src.cli.app as appmod
import src.paths as paths
from src.cli.app import app

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    import os

    monkeypatch.setattr(appmod, "load_env", lambda *a, **k: None)
    monkeypatch.setenv(paths._HOME_ENV, str(tmp_path))
    for var in list(os.environ):
        if var.startswith("ANY2WIKI_") and var != paths._HOME_ENV:
            monkeypatch.delenv(var, raising=False)
        if var.endswith("_API_KEY"):
            monkeypatch.delenv(var, raising=False)
    yield


def _config() -> dict:
    return yaml.safe_load(paths.config_path().read_text())


@pytest.mark.unit
def test_writes_a_config_the_resolver_can_read():
    """The end-to-end contract: what the wizard writes, a run must resolve."""
    result = runner.invoke(app, ["setup", "--provider", "openai",
                                 "--model", "openai:gpt-4o", "--yes"])

    assert result.exit_code == 0
    from src.llm_roles import get_model_spec
    assert get_model_spec("supervisor").model == "openai:gpt-4o"


@pytest.mark.unit
def test_cheap_auxiliary_covers_the_five_side_tasks():
    """The step the wizard exists for — nobody finds this by reading YAML."""
    runner.invoke(app, ["setup", "--provider", "openai", "--model", "openai:gpt-4o",
                        "--yes", "--cheap-aux"])

    aux = _config()["auxiliary"]
    assert set(aux) == {"subagent", "title", "summarize", "judge", "web_summarize"}
    assert "supervisor" not in aux, "the main agent must keep the good model"

    from src.llm_roles import get_model_spec
    assert get_model_spec("supervisor").model == "openai:gpt-4o"
    assert get_model_spec("judge").model == "openai:gpt-4o-mini"


@pytest.mark.unit
def test_no_cheap_aux_leaves_every_task_on_the_base_model():
    runner.invoke(app, ["setup", "--provider", "openai", "--model", "openai:gpt-4o",
                        "--yes", "--no-cheap-aux"])

    assert "auxiliary" not in _config()
    from src.llm_roles import get_model_spec
    assert get_model_spec("judge").model == "openai:gpt-4o"


@pytest.mark.unit
def test_missing_key_warns_but_still_writes():
    """The decision: warn, do not fail. require_keys() catches it at run time anyway."""
    result = runner.invoke(app, ["setup", "--provider", "openai",
                                 "--model", "openai:gpt-4o", "--yes"])

    assert result.exit_code == 0, "a missing key must not block writing a valid config"
    assert paths.config_path().exists()
    flat = " ".join(result.output.split())
    assert "OPENAI_API_KEY is not set" in flat
    assert "keys set OPENAI_API_KEY" in flat, "should say how to fix it"


@pytest.mark.unit
def test_existing_key_produces_no_warning(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-already-configured")
    result = runner.invoke(app, ["setup", "--provider", "openai",
                                 "--model", "openai:gpt-4o", "--yes"])

    assert "is not set" not in result.output


@pytest.mark.unit
def test_refuses_to_clobber_an_existing_config():
    paths.ensure_user_root()
    paths.config_path().write_text("model:\n  default: my-careful-choice\n")

    result = runner.invoke(app, ["setup", "--provider", "openai",
                                 "--model", "openai:gpt-4o", "--yes"])

    assert result.exit_code == 1
    assert "my-careful-choice" in paths.config_path().read_text()
    assert "--force" in result.output, "should say how to proceed anyway"


@pytest.mark.unit
def test_force_overwrites():
    paths.ensure_user_root()
    paths.config_path().write_text("model:\n  default: old\n")

    result = runner.invoke(app, ["setup", "--provider", "openai", "--model",
                                 "openai:gpt-4o", "--yes", "--force"])

    assert result.exit_code == 0
    assert _config()["model"]["default"] == "openai:gpt-4o"


@pytest.mark.unit
def test_creates_a_missing_user_root(tmp_path, monkeypatch):
    """A fresh install has no ~/.any2wiki."""
    target = tmp_path / "nothing" / "here"
    monkeypatch.setenv(paths._HOME_ENV, str(target))

    result = runner.invoke(app, ["setup", "--provider", "openai",
                                 "--model", "openai:gpt-4o", "--yes"])
    assert result.exit_code == 0
    assert (target / "config.yaml").exists()


@pytest.mark.unit
def test_unknown_provider_warns_but_proceeds():
    """An OpenAI-compatible endpoint is a legitimate setup we cannot enumerate."""
    result = runner.invoke(app, ["setup", "--provider", "some-gateway",
                                 "--model", "custom-model", "--yes"])

    assert result.exit_code == 0
    assert "Unknown provider" in " ".join(result.output.split())
    assert _config()["model"]["default"] == "custom-model"


@pytest.mark.unit
def test_interactive_path_accepts_typed_answers():
    """Driving the prompts, since --yes skips exactly the code a user would hit."""
    result = runner.invoke(app, ["setup"], input="openai\nopenai:gpt-4o\ny\nn\n")

    assert result.exit_code == 0
    assert _config()["model"]["default"] == "openai:gpt-4o"
    assert "auxiliary" in _config(), "answering yes to cheap-aux should write the block"


@pytest.mark.unit
def test_yes_defaults_to_anthropic_when_no_provider_given():
    result = runner.invoke(app, ["setup", "--yes"])

    assert result.exit_code == 0
    assert _config()["model"]["default"] == "claude-sonnet-4-6"
