"""The app must work on a non-Anthropic provider.

Two places used to hard-code Anthropic and are covered here:

* ``src/cli/_env.py:require_keys`` demanded ``ANTHROPIC_API_KEY`` unconditionally, so a
  correct OpenAI/Gemini setup was rejected for a key it would never use.
* ``eval/eval_utils.py`` called the raw ``anthropic`` SDK with a hard-coded judge model,
  bypassing the ``judge`` role entirely.

No network: the model is faked and the config is injected, so these run under
``pytest -m unit``.
"""

from __future__ import annotations

import os

import pytest
import typer


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip PAPER2WIKI_*/LITELLM_* and every provider key, so nothing leaks in."""
    for k in list(os.environ):
        if k.startswith(("PAPER2WIKI_", "LITELLM_")) or k.endswith("_API_KEY"):
            monkeypatch.delenv(k, raising=False)
    yield


def _set_config(monkeypatch, cfg):
    import src.llm_roles as lr

    monkeypatch.setattr(lr, "_config", lambda: cfg)


# --- require_keys asks for the *configured* provider's key ----------------------

@pytest.mark.unit
@pytest.mark.parametrize(
    "model, expected_key",
    [
        ("claude-sonnet-4-6", "ANTHROPIC_API_KEY"),
        ("anthropic:claude-sonnet-4-6", "ANTHROPIC_API_KEY"),
        ("openai:gpt-4o", "OPENAI_API_KEY"),
        ("gpt-4o", "OPENAI_API_KEY"),
        ("google_genai:gemini-2.0-flash", "GOOGLE_API_KEY"),
        ("gemini-2.0-flash", "GOOGLE_API_KEY"),
        ("groq:llama-3.3-70b", "GROQ_API_KEY"),
    ],
)
def test_required_key_follows_the_configured_provider(monkeypatch, model, expected_key):
    from src.cli._env import _required_model_key

    _set_config(monkeypatch, {"model": {"default": model}})
    assert _required_model_key() == expected_key


@pytest.mark.unit
@pytest.mark.parametrize(
    "cfg, why",
    [
        ({"model": {"default": "ollama:llama3"}}, "local provider needs no key"),
        ({"model": {"default": "some-unknown-model"}}, "provider not inferable"),
        ({"model": {"default": "gpt-4o", "api_key": "sk-inline"}}, "key supplied in config"),
        ({"model": {"default": "gpt-4o", "base_url": "http://localhost:4000"}},
         "custom endpoint owns auth"),
    ],
)
def test_no_key_demanded_when_we_cannot_or_should_not_say(monkeypatch, cfg, why):
    """Fail open: a false refusal blocks a working setup, which was the original bug."""
    from src.cli._env import _required_model_key

    _set_config(monkeypatch, cfg)
    assert _required_model_key() is None, why


@pytest.mark.unit
def test_litellm_gateway_demands_no_provider_key(monkeypatch):
    """The gateway forces provider=openai, but the *proxy* authenticates, not us."""
    from src.cli._env import _required_model_key

    _set_config(monkeypatch, {"model": {"default": "claude-sonnet-4-6"}})
    monkeypatch.setenv("PAPER2WIKI_LLM_GATEWAY", "litellm")
    assert _required_model_key() is None


@pytest.mark.unit
def test_openai_setup_is_not_blocked_by_missing_anthropic_key(monkeypatch):
    """The regression itself: this exited 1 demanding ANTHROPIC_API_KEY."""
    from src.cli._env import require_keys

    _set_config(monkeypatch, {"model": {"default": "openai:gpt-4o"}})
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    require_keys(eval_mode=True)  # must not raise


@pytest.mark.unit
def test_wrong_provider_key_still_fails_and_names_the_right_one(monkeypatch, capsys):
    from src.cli._env import require_keys

    _set_config(monkeypatch, {"model": {"default": "openai:gpt-4o"}})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")  # the *wrong* key
    with pytest.raises(typer.Exit):
        require_keys(eval_mode=True)
    assert "OPENAI_API_KEY" in capsys.readouterr().err


@pytest.mark.unit
def test_unreadable_config_falls_back_to_anthropic(monkeypatch):
    """llm_roles' zero-config fallback is Claude, so ask for its key rather than nothing."""
    import src.llm_roles as lr
    from src.cli._env import _required_model_key

    def _boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr(lr, "_config", _boom)
    assert _required_model_key() == "ANTHROPIC_API_KEY"


# --- the eval judges go through the judge role, not the raw SDK ------------------

class _FakeStructured:
    """Stands in for ``model.with_structured_output(schema)``."""

    def __init__(self, schema, payload, raises=None):
        self._schema, self._payload, self._raises = schema, payload, raises
        self.seen: list = []

    def invoke(self, messages):
        if self._raises:
            raise self._raises
        self.seen.append(messages)
        return self._schema(**self._payload)


class _FakeModel:
    def __init__(self, payload, raises=None):
        self._payload, self._raises = payload, raises
        self.structured: _FakeStructured | None = None

    def with_structured_output(self, schema):
        self.structured = _FakeStructured(schema, self._payload, self._raises)
        return self.structured


def _patch_judge(monkeypatch, model):
    import eval.eval_utils as eu

    monkeypatch.setattr(eu, "_judge", lambda: model)
    return model


@pytest.mark.unit
def test_llm_judge_returns_a_langsmith_result_dict(monkeypatch):
    import eval.eval_utils as eu

    model = _patch_judge(monkeypatch, _FakeModel({"score": 1, "reason": "grounded"}))
    out = eu.llm_judge("be strict", "the answer", key="faithfulness")

    assert out == {"key": "faithfulness", "score": 1.0, "comment": "grounded"}
    assert isinstance(out["score"], float)
    # system prompt and content both reach the model, as two messages
    roles = [m["role"] for m in model.structured.seen[0]]
    assert roles == ["system", "user"]


@pytest.mark.unit
def test_llm_judge_truncates_oversized_input(monkeypatch):
    import eval.eval_utils as eu

    model = _patch_judge(monkeypatch, _FakeModel({"score": 0, "reason": "too long"}))
    eu.llm_judge("sys", "x" * 5000, key="k", max_input_chars=100)

    assert len(model.structured.seen[0][1]["content"]) == 100


@pytest.mark.unit
def test_llm_judge_reports_errors_instead_of_raising(monkeypatch):
    """A judge failure must score 0 and explain, never abort the eval run."""
    import eval.eval_utils as eu

    _patch_judge(monkeypatch, _FakeModel({}, raises=RuntimeError("provider exploded")))
    out = eu.llm_judge("sys", "answer", key="k")

    assert out["key"] == "k" and out["score"] == 0.0
    assert "judge error" in out["comment"] and "provider exploded" in out["comment"]


@pytest.mark.unit
def test_llm_judge_multi_returns_one_result_per_key(monkeypatch):
    import eval.eval_utils as eu

    payload = {
        "grounded": 1, "grounded_reason": "cites the source",
        "complete": 0, "complete_reason": "missing the limitations",
    }
    _patch_judge(monkeypatch, _FakeModel(payload))
    out = eu.llm_judge_multi("rubric", "answer", keys=["grounded", "complete"])

    assert [r["key"] for r in out] == ["grounded", "complete"]
    assert [r["score"] for r in out] == [1.0, 0.0]
    assert out[0]["comment"] == "cites the source"


@pytest.mark.unit
def test_llm_judge_multi_reports_errors_for_every_key(monkeypatch):
    import eval.eval_utils as eu

    _patch_judge(monkeypatch, _FakeModel({}, raises=RuntimeError("timeout")))
    out = eu.llm_judge_multi("rubric", "answer", keys=["a", "b"])

    assert [r["key"] for r in out] == ["a", "b"]
    assert all(r["score"] == 0.0 and "judge error" in r["comment"] for r in out)


@pytest.mark.unit
def test_eval_utils_does_not_import_the_anthropic_sdk():
    """The regression guard: the judge must resolve through the `judge` role."""
    import pathlib

    src = pathlib.Path(__file__).resolve().parents[1] / "eval" / "eval_utils.py"
    text = src.read_text(encoding="utf-8")
    assert "import anthropic" not in text
    assert "anthropic.Anthropic(" not in text


@pytest.mark.unit
def test_judge_resolves_through_the_judge_role(monkeypatch):
    """`auxiliary.judge` must actually reach the model the judges use."""
    import eval.eval_utils as eu

    _set_config(monkeypatch, {
        "model": {"default": "claude-sonnet-4-6"},
        "auxiliary": {"judge": {"model": "openai:gpt-4o-mini"}},
    })
    captured = {}

    def _fake_set_up_llms(spec):
        captured["model"] = spec.model
        return _FakeModel({"score": 1, "reason": "ok"})

    import src.agents.llms as llms

    monkeypatch.setattr(llms, "set_up_llms", _fake_set_up_llms)
    eu._judge()
    assert captured["model"] == "openai:gpt-4o-mini"


# --- the --model flag ---------------------------------------------------------

@pytest.mark.unit
def test_apply_env_sets_the_global_model_var(monkeypatch):
    from src.cli._env import apply_env

    apply_env(None, None, "openai:gpt-4o")
    assert os.environ["PAPER2WIKI_MODEL"] == "openai:gpt-4o"


@pytest.mark.unit
def test_apply_env_without_model_leaves_env_untouched(monkeypatch):
    """Omitting the flag must not clobber a model set in .env."""
    from src.cli._env import apply_env

    monkeypatch.setenv("PAPER2WIKI_MODEL", "from-dotenv")
    apply_env(None, None, None)
    assert os.environ["PAPER2WIKI_MODEL"] == "from-dotenv"


@pytest.mark.unit
def test_model_flag_reaches_the_resolved_spec(monkeypatch):
    import src.llm_roles as lr
    from src.cli._env import apply_env

    _set_config(monkeypatch, {"model": {"default": "claude-sonnet-4-6"}})
    apply_env(None, None, "openai:gpt-4o")
    assert lr.get_model_spec("supervisor").model == "openai:gpt-4o"


@pytest.mark.unit
def test_model_flag_is_level_3_and_does_not_override_task_config(monkeypatch):
    """`--model` sets the BASE model; `auxiliary.<task>.model` still wins.

    Copied from hermes-agent, whose config has the same shape: its `--model` overrides
    `model.default` only, because side tasks are pinned on purpose (vision, cheap
    summarisation) and retargeting them silently would break them.

    Pinned here so a later change can't quietly promote the flag to level 1.
    """
    import src.llm_roles as lr
    from src.cli._env import apply_env

    _set_config(monkeypatch, {
        "model": {"default": "claude-sonnet-4-6"},
        "auxiliary": {"judge": {"model": "claude-haiku-4-5-20251001"}},
    })
    apply_env(None, None, "openai:gpt-4o")

    assert lr.get_model_spec("supervisor").model == "openai:gpt-4o"      # no task pin
    assert lr.get_model_spec("judge").model == "claude-haiku-4-5-20251001"  # task pin wins


@pytest.mark.unit
def test_task_env_var_still_beats_the_model_flag(monkeypatch):
    """Level 1 outranks the flag at level 3."""
    import src.llm_roles as lr
    from src.cli._env import apply_env

    _set_config(monkeypatch, {"model": {"default": "claude-sonnet-4-6"}})
    monkeypatch.setenv("PAPER2WIKI_MODEL_JUDGE", "level1-wins")
    apply_env(None, None, "openai:gpt-4o")

    assert lr.get_model_spec("judge").model == "level1-wins"
    assert lr.get_model_spec("supervisor").model == "openai:gpt-4o"


@pytest.mark.unit
def test_model_flag_changes_which_api_key_is_required(monkeypatch):
    """The flag must reach require_keys, not just the model spec."""
    from src.cli._env import _required_model_key, apply_env

    _set_config(monkeypatch, {"model": {"default": "claude-sonnet-4-6"}})
    assert _required_model_key() == "ANTHROPIC_API_KEY"

    apply_env(None, None, "openai:gpt-4o")
    assert _required_model_key() == "OPENAI_API_KEY"


@pytest.mark.unit
@pytest.mark.parametrize(
    "command",
    [["repl"], ["chat"], ["serve"], ["sessions", "resume"], ["config", "show"]],
)
def test_model_flag_is_registered_on_every_model_using_command(command):
    from typer.testing import CliRunner

    from src.cli.app import app

    result = CliRunner().invoke(app, command + ["--help"])
    assert result.exit_code == 0
    assert "--model" in result.output
    assert "-m" in result.output


@pytest.mark.unit
def test_fetch_has_no_model_flag():
    """`fetch` never calls a model, so the flag would be a lie."""
    from typer.testing import CliRunner

    from src.cli.app import app

    result = CliRunner().invoke(app, ["fetch", "--help"])
    assert result.exit_code == 0
    assert "--model" not in result.output
