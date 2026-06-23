"""Per-task model selection — one base LLM drives everything, tasks can override.

The user picks **one** base model (``model.default`` + its API key); it becomes the
model for the supervisor, the subagents, and every auxiliary task. Any single
auxiliary task can be pointed at a different provider/model/endpoint/key without
touching the rest.

Config shape (``config.yaml``)::

    model:
      default: claude-sonnet-4-6
      provider: anthropic        # 'auto'/omit → infer from the model name
      # base_url, api_key optional (else the provider's env var is used)

    auxiliary:
      web_summarize:             # one block per task; all fields optional
        provider: anthropic
        model: claude-haiku-4-5-20251001
        base_url: ''
        api_key: ''
        timeout: 120
        extra_body: {}

Resolution for a task:

  model    = ``PAPER2WIKI_MODEL_<TASK>`` env > ``auxiliary.<task>.model``
             > ``PAPER2WIKI_MODEL`` env > ``model.default`` > built-in fallback
  provider = ``auxiliary.<task>.provider`` > ``model.provider``  ('auto'/'' → infer)
  base_url/api_key = task value > base value > None  (None → provider env var)
  timeout / extra_body = task value > default

Model strings pass to ``init_chat_model``: any LangChain value works, incl. the
``provider:model`` form (``openai:gpt-4o``). Router/multi-key features
(fallbacks, credential pools) are intentionally NOT here — they belong to the
LiteLLM gateway layer, which the SDK path can't honour.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

VALID_ROLES = frozenset({"supervisor", "subagent", "title", "summarize", "judge", "web_summarize"})

# Zero-config fallback only — used when the user configures nothing. Requires an
# Anthropic key; real deployments set model.default / PAPER2WIKI_MODEL instead.
_FALLBACK_MODEL = "claude-sonnet-4-6"

_AUTO = {None, "", "auto"}


@dataclass
class ModelSpec:
    """Everything ``set_up_llms`` needs to build one task's model."""
    model: str
    provider: str | None = None      # None → let init_chat_model infer
    base_url: str | None = None
    api_key: str | None = None
    timeout: float | None = None
    extra_body: dict = field(default_factory=dict)


def _config() -> dict:
    try:
        from src.tools.web_tools.registry import load_config_file

        return load_config_file() or {}
    except Exception:
        return {}


def _model_block() -> dict:
    blk = _config().get("model")
    return blk if isinstance(blk, dict) else {}


def _aux_block(role: str) -> dict:
    aux = _config().get("auxiliary") or {}
    return aux.get(role) or {} if isinstance(aux, dict) else {}


def get_base_model() -> str:
    """The user's chosen LLM string — the default for every task."""
    env = os.environ.get("PAPER2WIKI_MODEL", "").strip()
    if env:
        return env
    default = _model_block().get("default")
    if default:
        return str(default).strip()
    return _FALLBACK_MODEL


def get_model_spec(role: str) -> ModelSpec:
    """Resolve the full model spec for a task ``role`` (see module docstring)."""
    if role not in VALID_ROLES:
        raise ValueError(f"Unknown model role {role!r}; valid: {sorted(VALID_ROLES)}")

    base, aux = _model_block(), _aux_block(role)

    # model name: per-role env > aux.model > base model
    env_role = os.environ.get(f"PAPER2WIKI_MODEL_{role.upper()}", "").strip()
    model = env_role or str(aux.get("model") or "").strip() or get_base_model()

    def _pick(key):  # task value, else base value, else None
        v = aux.get(key) if aux.get(key) not in (None, "") else base.get(key)
        return v if v not in (None, "") else None

    provider = aux.get("provider") if aux.get("provider") not in _AUTO else base.get("provider")
    provider = provider if provider not in _AUTO else None

    timeout = aux.get("timeout")
    spec = ModelSpec(
        model=model,
        provider=provider,
        base_url=_pick("base_url"),
        api_key=_pick("api_key"),
        timeout=float(timeout) if timeout not in (None, "") else None,
        extra_body=dict(aux.get("extra_body") or {}),
    )
    return _apply_gateway(spec)


_GATEWAY_DEFAULT_BASE_URL = "http://localhost:4000"


def _apply_gateway(spec: ModelSpec) -> ModelSpec:
    """Route a spec through the LiteLLM proxy when ``PAPER2WIKI_LLM_GATEWAY=litellm``.

    The "lie to LangChain" trick: force ``provider=openai`` + ``base_url=<proxy>`` so the request
    becomes a generic OpenAI-shaped call to the proxy (which then authenticates, meters, caps,
    fallbacks, and routes it to the real model). ``model`` is unchanged — the proxy's ``model_list``
    aliases resolve it. ``api_key`` is the per-tenant **virtual key**, never the real provider key.

    Flag off (default) → returns the spec untouched (today's direct-to-provider behavior).
    """
    if os.environ.get("PAPER2WIKI_LLM_GATEWAY", "").strip().lower() != "litellm":
        return spec

    # Per-tenant cache scoping: if this role opted into caching (extra_body.cache.use-cache),
    # stamp the tenant namespace so semantic-cache hits never cross tenants.
    extra_body = dict(spec.extra_body or {})
    cache = extra_body.get("cache")
    namespace = os.environ.get("LITELLM_CACHE_NAMESPACE", "").strip()
    if isinstance(cache, dict) and cache.get("use-cache") and namespace:
        extra_body["cache"] = {**cache, "namespace": namespace}

    return ModelSpec(
        model=spec.model,  # unchanged — proxy alias resolves it
        provider="openai",
        base_url=os.environ.get("LITELLM_BASE_URL", "").strip() or _GATEWAY_DEFAULT_BASE_URL,
        api_key=os.environ.get("LITELLM_API_KEY", "").strip() or None,
        timeout=spec.timeout,
        extra_body=extra_body,
    )


def get_model(role: str) -> str:
    """Just the resolved model name for a task (convenience over ``get_model_spec``)."""
    return get_model_spec(role).model
