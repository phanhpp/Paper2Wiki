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

Which model a task gets
-----------------------

Five levels. The **first one that exists wins**, i.e. priority order::

    Task-Specific Env Var → Task Config → Global Env Var → Base Config → Default Fallback

    level                     lives in      example                      applies to
    ------------------------  ------------  ---------------------------  ----------
    1. Task-Specific Env Var  .env          ANY2WIKI_MODEL_SUMMARIZE   one task
    2. Task Config            config.yaml   auxiliary.summarize.model    one task
    3. Global Env Var         .env          ANY2WIKI_MODEL             every task
    4. Base Config            config.yaml   model.default                every task
    5. Default Fallback       built in      _FALLBACK_MODEL              every task

The pattern behind the order: **task beats global, and env beats config.**

Levels 4 and 3 are how you choose your model; levels 2 and 1 are how you make one
task differ — permanently in the file, or temporarily with an env var.

Non-Model Settings - All live in config.yaml 

Besides the model name, other parameters (like keys, URLs, and timeouts) follow these lookup rules:

    provider:   Task Config  -->  Global 'model:' block  -->  None
            (None, '', or 'auto' means automatically infer from the model name)

    base_url:   Task Config  -->  Global 'model:' block  -->  None
    api_key:    Task Config  -->  Global 'model:' block  -->  None
                (None means LangChain reads the provider's standard env var, e.g., OPENAI_API_KEY)

    timeout:    Task Config ONLY  (Not inherited from global 'model:' block)
    extra_body: Task Config ONLY  (Not inherited from global 'model:' block)

⚬	Task Config: Specific overrides for individual tasks in config.yaml (under the auxiliary: block).
⚬	Important: timeout and extra_body are isolated. Setting timeout in the global 'model:' block 
    will not pass down to tasks like summarize or judge. 
    If a task needs a custom timeout or extra parameters, you must set them inside that task's own config.

Notes
-----

Model strings go straight to ``init_chat_model``, so any LangChain value works,
including the ``provider:model`` form (``openai:gpt-4o``).

Fallback chains and credential pools are deliberately **not** here. They belong to
the LiteLLM gateway layer, which this SDK path cannot express.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

VALID_ROLES = frozenset({"supervisor", "subagent", "title", "summarize", "judge", "web_summarize"})

# Zero-config fallback only — used when the user configures nothing. Requires an
# Anthropic key; real deployments set model.default / ANY2WIKI_MODEL instead.
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
    env = os.environ.get("ANY2WIKI_MODEL", "").strip()
    if env:
        return env
    default = _model_block().get("default")
    if default:
        return str(default).strip()
    return _FALLBACK_MODEL


def _builtin_providers() -> frozenset[str]:
    """Provider names ``init_chat_model`` recognises as a ``provider:model`` prefix."""
    try:
        from langchain.chat_models.base import _BUILTIN_PROVIDERS

        return frozenset(_BUILTIN_PROVIDERS)
    except Exception:  # pragma: no cover - private name; fall back to the common ones
        return frozenset({
            "openai", "anthropic", "azure_openai", "google_genai", "google_vertexai",
            "bedrock", "bedrock_converse", "cohere", "fireworks", "together", "mistralai",
            "huggingface", "groq", "ollama", "deepseek", "xai", "perplexity",
        })


def _explicit_provider_prefix(model: str) -> str | None:
    """The ``provider:`` prefix of a model string, when it names a real provider.

    ``init_chat_model`` splits ``openai:gpt-4o`` into provider + model **only when no
    ``model_provider`` is passed alongside**. So a configured ``model.provider`` silently
    beat the prefix the user typed, and Anthropic was asked for a model literally named
    ``openai:gpt-4o`` — a 404. Spotting the prefix here lets it win instead.

    Returns None when there is no colon, or the part before it is not a known provider
    (model tags like ``qwen3.5:397b`` must not be mistaken for one).
    """
    if ":" not in model:
        return None
    prefix = model.split(":", 1)[0]
    return prefix if prefix in _builtin_providers() else None


def get_model_spec(role: str) -> ModelSpec:
    """Resolve the full model spec for a task ``role`` (see module docstring)."""
    if role not in VALID_ROLES:
        raise ValueError(f"Unknown model role {role!r}; valid: {sorted(VALID_ROLES)}")

    base, aux = _model_block(), _aux_block(role)

    # model name: per-role env > aux.model > base model
    env_role = os.environ.get(f"ANY2WIKI_MODEL_{role.upper()}", "").strip()
    model = env_role or str(aux.get("model") or "").strip() or get_base_model()

    def _pick(key):  # task value, else base value, else None
        v = aux.get(key) if aux.get(key) not in (None, "") else base.get(key)
        return v if v not in (None, "") else None

    provider = aux.get("provider") if aux.get("provider") not in _AUTO else base.get("provider")
    provider = provider if provider not in _AUTO else None

    # An explicit `provider:model` prefix wins over a configured provider — being
    # specific in the model string is the stronger signal. Leave provider unset so
    # init_chat_model does the split itself, and drop base_url/api_key when they came
    # from the *other* provider's config block, where they would not work anyway.
    prefix = _explicit_provider_prefix(model)
    switched = bool(prefix) and prefix != provider
    if prefix:
        provider = None

    timeout = aux.get("timeout")
    spec = ModelSpec(
        model=model,
        provider=provider,
        base_url=None if switched else _pick("base_url"),
        api_key=None if switched else _pick("api_key"),
        timeout=float(timeout) if timeout not in (None, "") else None,
        extra_body=dict(aux.get("extra_body") or {}),
    )
    return _apply_gateway(spec, role)


_GATEWAY_DEFAULT_BASE_URL = "http://localhost:4000"

# Idempotent, stateless tasks safe to semantic-cache. The supervisor/subagent are
# deliberately excluded — caching a stateful tool-calling turn could replay a stale
# tool call or return a wrong-context answer.
_CACHEABLE_ROLES = frozenset({"web_summarize", "summarize", "title", "judge"})


def _apply_gateway(spec: ModelSpec, role: str) -> ModelSpec:
    """Route a spec through the LiteLLM proxy when ``ANY2WIKI_LLM_GATEWAY=litellm``.

    The "lie to LangChain" trick: force ``provider=openai`` + ``base_url=<proxy>`` so the request
    becomes a generic OpenAI-shaped call to the proxy (which then authenticates, meters, caps,
    fallbacks, and routes it to the real model). ``model`` is unchanged — the proxy's ``model_list``
    aliases resolve it. ``api_key`` is the per-tenant **virtual key**, never the real provider key.

    For ``_CACHEABLE_ROLES`` it also opts the request into the proxy's semantic cache
    (``cache: {use-cache: true}`` — the proxy runs ``mode: default_off``) with a per-tenant
    ``namespace`` so hits never cross tenants. The cache directive is injected **only here**, on the
    gateway path, so it never reaches a direct provider (which would reject it).

    Flag off (default) → returns the spec untouched (today's direct-to-provider behavior).
    """
    if os.environ.get("ANY2WIKI_LLM_GATEWAY", "").strip().lower() != "litellm":
        return spec

    extra_body = dict(spec.extra_body or {})
    if role in _CACHEABLE_ROLES:
        cache = {"use-cache": True}
        namespace = os.environ.get("LITELLM_CACHE_NAMESPACE", "").strip()
        if namespace:
            cache["namespace"] = namespace  # scope hits per tenant (no cross-tenant leakage)
        extra_body["cache"] = cache

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
