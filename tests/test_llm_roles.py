"""Unit tests for per-task model selection (ModelSpec) and set_up_llms."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Strip PAPER2WIKI_* and LITELLM_* env so tests are deterministic."""
    import os

    for k in list(os.environ):
        if k.startswith("PAPER2WIKI_") or k.startswith("LITELLM_"):
            monkeypatch.delenv(k, raising=False)
    yield


def _set_config(monkeypatch, cfg):
    import src.llm_roles as lr

    monkeypatch.setattr(lr, "_config", lambda: cfg)


@pytest.mark.unit
def test_base_model_fallback_when_unconfigured(monkeypatch):
    import src.llm_roles as lr

    _set_config(monkeypatch, {})
    assert lr.get_base_model() == lr._FALLBACK_MODEL
    for role in lr.VALID_ROLES:
        assert lr.get_model(role) == lr._FALLBACK_MODEL


@pytest.mark.unit
def test_base_model_precedence_env_over_config(monkeypatch):
    import src.llm_roles as lr

    _set_config(monkeypatch, {"model": {"default": "config:model"}})
    assert lr.get_base_model() == "config:model"

    monkeypatch.setenv("PAPER2WIKI_MODEL", "openai:gpt-4o")
    assert lr.get_base_model() == "openai:gpt-4o"


@pytest.mark.unit
def test_role_inherits_base_unless_overridden(monkeypatch):
    import src.llm_roles as lr

    _set_config(monkeypatch, {"model": {"default": "openai:gpt-4o"}})
    assert lr.get_model("supervisor") == "openai:gpt-4o"
    assert lr.get_model("subagent") == "openai:gpt-4o"

    _set_config(monkeypatch, {
        "model": {"default": "openai:gpt-4o"},
        "auxiliary": {"subagent": {"model": "openai:gpt-4o-mini"}},
    })
    assert lr.get_model("subagent") == "openai:gpt-4o-mini"
    assert lr.get_model("title") == "openai:gpt-4o"

    monkeypatch.setenv("PAPER2WIKI_MODEL_TITLE", "anthropic:claude-haiku-4-5")
    assert lr.get_model("title") == "anthropic:claude-haiku-4-5"


@pytest.mark.unit
def test_full_spec_resolution(monkeypatch):
    """A task block supplies provider/base_url/api_key/timeout/extra_body; base fills gaps."""
    import src.llm_roles as lr

    _set_config(monkeypatch, {
        "model": {"default": "claude-sonnet-4-6", "provider": "anthropic", "api_key": "base-key"},
        "auxiliary": {
            "summarize": {
                "provider": "openai",
                "model": "google/gemini-2.5-flash",
                "base_url": "https://openrouter.ai/api/v1",
                "timeout": 60,
                "extra_body": {"reasoning": {"effort": "low"}},
            }
        },
    })

    spec = lr.get_model_spec("summarize")
    assert spec.model == "google/gemini-2.5-flash"
    assert spec.provider == "openai"
    assert spec.base_url == "https://openrouter.ai/api/v1"
    assert spec.api_key == "base-key"          # inherited from base
    assert spec.timeout == 60.0
    assert spec.extra_body == {"reasoning": {"effort": "low"}}


@pytest.mark.unit
def test_auto_provider_inherits_and_infers(monkeypatch):
    """provider 'auto' on the task falls back to base; base 'auto' → None (infer)."""
    import src.llm_roles as lr

    _set_config(monkeypatch, {
        "model": {"default": "claude-sonnet-4-6", "provider": "auto"},
        "auxiliary": {"title": {"provider": "auto"}},
    })
    assert lr.get_model_spec("title").provider is None  # both auto → infer


@pytest.mark.unit
def test_get_model_rejects_unknown_role(monkeypatch):
    import src.llm_roles as lr

    _set_config(monkeypatch, {})
    with pytest.raises(ValueError):
        lr.get_model("not_a_role")


@pytest.mark.unit
def test_gateway_off_leaves_spec_unrouted(monkeypatch):
    """No PAPER2WIKI_LLM_GATEWAY → spec keeps its configured provider/base_url (direct path)."""
    import src.llm_roles as lr

    _set_config(monkeypatch, {"model": {"default": "claude-sonnet-4-6", "provider": "anthropic"}})
    spec = lr.get_model_spec("supervisor")
    assert spec.provider == "anthropic"
    assert spec.base_url is None


@pytest.mark.unit
def test_gateway_on_routes_every_role_to_proxy(monkeypatch):
    """Flag on → provider forced to openai, base_url/api_key from env, model unchanged."""
    import src.llm_roles as lr

    _set_config(monkeypatch, {"model": {"default": "claude-sonnet-4-6", "provider": "anthropic"}})
    monkeypatch.setenv("PAPER2WIKI_LLM_GATEWAY", "litellm")
    monkeypatch.setenv("LITELLM_BASE_URL", "http://localhost:4000")
    monkeypatch.setenv("LITELLM_API_KEY", "sk-virtual-tenant")

    spec = lr.get_model_spec("supervisor")
    assert spec.provider == "openai"                 # "lie to LangChain"
    assert spec.base_url == "http://localhost:4000"
    assert spec.api_key == "sk-virtual-tenant"       # the virtual key, not the real one
    assert spec.model == "claude-sonnet-4-6"         # unchanged — proxy alias resolves it


@pytest.mark.unit
def test_gateway_defaults_base_url(monkeypatch):
    """Flag on with no LITELLM_BASE_URL → defaults to the local proxy port."""
    import src.llm_roles as lr

    _set_config(monkeypatch, {"model": {"default": "m"}})
    monkeypatch.setenv("PAPER2WIKI_LLM_GATEWAY", "litellm")
    assert lr.get_model_spec("title").base_url == lr._GATEWAY_DEFAULT_BASE_URL


@pytest.mark.unit
def test_gateway_caches_only_idempotent_roles(monkeypatch):
    """Idempotent roles get a per-tenant cache opt-in injected; supervisor/subagent never do."""
    import src.llm_roles as lr

    _set_config(monkeypatch, {"model": {"default": "m"}})
    monkeypatch.setenv("PAPER2WIKI_LLM_GATEWAY", "litellm")
    monkeypatch.setenv("LITELLM_CACHE_NAMESPACE", "tenant-1")

    for role in ("web_summarize", "summarize", "title", "judge"):
        assert lr.get_model_spec(role).extra_body["cache"] == {"use-cache": True, "namespace": "tenant-1"}

    # Stateful / tool-calling roles are never cached.
    assert "cache" not in lr.get_model_spec("supervisor").extra_body
    assert "cache" not in lr.get_model_spec("subagent").extra_body


@pytest.mark.unit
def test_gateway_cache_without_namespace(monkeypatch):
    """Cacheable role, no LITELLM_CACHE_NAMESPACE → cache opt-in kept, namespace not invented."""
    import src.llm_roles as lr

    _set_config(monkeypatch, {"model": {"default": "m"}})
    monkeypatch.setenv("PAPER2WIKI_LLM_GATEWAY", "litellm")

    assert lr.get_model_spec("summarize").extra_body["cache"] == {"use-cache": True}


@pytest.mark.unit
def test_gateway_off_injects_no_cache(monkeypatch):
    """Direct mode (flag off) never adds a cache directive — it would break a real provider."""
    import src.llm_roles as lr

    _set_config(monkeypatch, {"model": {"default": "m"}})
    assert "cache" not in lr.get_model_spec("summarize").extra_body


@pytest.mark.unit
def test_set_up_llms_spec_applies_all_fields(monkeypatch):
    """set_up_llms(ModelSpec) wires provider/base_url/api_key/timeout/extra_body."""
    import src.agents.llms as llms
    from src.llm_roles import ModelSpec

    captured = {}

    def _fake_init(**kwargs):
        captured.update(kwargs)
        return "MODEL"

    monkeypatch.setattr(llms, "init_chat_model", _fake_init)

    spec = ModelSpec(
        model="google/gemini-2.5-flash", provider="openai",
        base_url="https://openrouter.ai/api/v1", api_key="sk-or",
        timeout=60, extra_body={"reasoning": {"effort": "low"}},
    )
    llms.set_up_llms(spec)
    assert captured["model"] == "google/gemini-2.5-flash"
    assert captured["model_provider"] == "openai"
    assert captured["base_url"] == "https://openrouter.ai/api/v1"
    assert captured["api_key"] == "sk-or"
    assert captured["timeout"] == 60
    # extra_body rides the dedicated `extra_body` param, NOT model_kwargs (where `cache` would
    # collide with ChatOpenAI's reserved field).
    assert captured["extra_body"] == {"reasoning": {"effort": "low"}}
    assert "reasoning" not in captured.get("model_kwargs", {})
    # Non-Anthropic provider → Anthropic-only knobs stripped.
    assert "effort" not in captured and "thinking" not in captured


@pytest.mark.unit
def test_set_up_llms_spec_known_anthropic_keeps_effort(monkeypatch):
    """A known Claude model via spec keeps its tuned effort when provider is anthropic."""
    import src.agents.llms as llms
    from src.llm_roles import ModelSpec

    captured = {}
    monkeypatch.setattr(llms, "init_chat_model", lambda **kw: captured.update(kw))

    llms.set_up_llms(ModelSpec(model="claude-sonnet-4-6", provider="anthropic"))
    assert captured["effort"] == "medium"


@pytest.mark.unit
def test_resolves_from_real_config_file(tmp_path, monkeypatch):
    """End-to-end: model/auxiliary are read from the whole config.yaml, not just `web`."""
    import src.llm_roles as lr

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "model:\n"
        "  default: claude-sonnet-4-6\n"
        "  provider: anthropic\n"
        "auxiliary:\n"
        "  title:\n"
        "    provider: anthropic\n"
        "    model: claude-haiku-4-5-20251001\n"
        "    timeout: 120\n"
        "web:\n"
        "  backend: firecrawl\n"
    )
    monkeypatch.setenv("PAPER2WIKI_CONFIG", str(cfg))

    assert lr.get_base_model() == "claude-sonnet-4-6"
    spec = lr.get_model_spec("title")
    assert spec.model == "claude-haiku-4-5-20251001"  # from auxiliary block (not base)
    assert spec.provider == "anthropic"
    assert spec.timeout == 120.0
    # A task with no block inherits the base model + provider.
    assert lr.get_model_spec("summarize").model == "claude-sonnet-4-6"


@pytest.mark.unit
def test_set_up_llms_string_paths_unchanged(monkeypatch):
    """String API still works: generic defaults for unknown, tuned for known."""
    import src.agents.llms as llms

    captured = {}
    monkeypatch.setattr(llms, "init_chat_model", lambda **kw: captured.update(kw) or "M")

    llms.set_up_llms("openai:gpt-4o")
    assert captured["model"] == "openai:gpt-4o"
    assert captured["max_tokens"] == llms._GENERIC_DEFAULTS["max_tokens"]
    assert "effort" not in captured
