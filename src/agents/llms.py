# from langchain_anthropic import ChatAnthropic
from langchain.chat_models import init_chat_model
from dotenv import load_dotenv
from deepagents import (
    GeneralPurposeSubagentProfile,
    HarnessProfile,
    register_harness_profile,
)

load_dotenv()

MODEL_CONFIG = {
    "claude-haiku-4-5-20251001": {
        "model": "claude-haiku-4-5-20251001",
        "max_retries": 8,
        "timeout": 120.0,
        "max_tokens": 8000,
    },
    "claude-sonnet-4-6": {
        "model": "claude-sonnet-4-6",
        "max_retries": 8,
        "timeout": 120.0,
        "max_tokens": 8000,
        "effort": "medium",
        #"thinking": {"type": "adaptive"},
    },
}

# Keys accepted as top-level init_chat_model kwargs; everything else goes to model_kwargs.
_TOP_LEVEL_KEYS = {"model", "max_retries", "timeout", "max_tokens", "effort", "thinking"}

register_harness_profile(
    "anthropic:claude-sonnet-4-6",
    HarnessProfile(
        system_prompt_suffix="**Remember to start with reading relevant skill if any**",
        # excluded_tools={"execute"},
        # excluded_middleware={"SummarizationMiddleware"},
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
    ),
)

# Defaults applied to models not pre-registered in MODEL_CONFIG (user-chosen
# provider models). Anthropic-only knobs (effort/thinking) are intentionally
# omitted so non-Anthropic providers don't receive invalid params.
_GENERIC_DEFAULTS = {"max_retries": 8, "timeout": 120.0, "max_tokens": 8000}


def _is_anthropic(model: str, provider: str | None) -> bool:
    if provider:
        return provider == "anthropic"
    return "claude" in model or model.startswith("anthropic")


def set_up_llms(model, **kwargs):
    """Build a LangChain chat model.

    ``model`` is either a model-name string or a :class:`~src.llm_roles.ModelSpec`.

    - String: known models (``MODEL_CONFIG``) use their tuned settings; any other
      string passes straight to ``init_chat_model`` with generic defaults. Supports
      the ``provider:model`` form (e.g. ``openai:gpt-4o``).
    - ``ModelSpec``: a per-task spec from ``llm_roles`` — applies provider,
      ``base_url``, ``api_key``, ``timeout`` and ``extra_body`` on top of the tuned
      (or generic) base for that model.

    Anthropic-only params (``effort``/``thinking``) are stripped when the resolved
    provider isn't Anthropic, so other providers don't choke.
    """
    from src.llm_roles import ModelSpec

    if isinstance(model, ModelSpec):
        spec = model
        base = MODEL_CONFIG.get(spec.model, {"model": spec.model, **_GENERIC_DEFAULTS})
        top = {k: v for k, v in base.items() if k in _TOP_LEVEL_KEYS}
        model_kwargs = {k: v for k, v in base.items() if k not in _TOP_LEVEL_KEYS}

        top["model"] = spec.model
        if spec.provider:
            top["model_provider"] = spec.provider
        if spec.base_url:
            top["base_url"] = spec.base_url
        if spec.api_key:
            top["api_key"] = spec.api_key
        if spec.timeout is not None:
            top["timeout"] = spec.timeout
        if not _is_anthropic(spec.model, spec.provider):
            top.pop("effort", None)
            top.pop("thinking", None)

        return init_chat_model(**top, model_kwargs=model_kwargs | dict(spec.extra_body) | kwargs)

    if model in MODEL_CONFIG:
        cfg = MODEL_CONFIG[model]
        top_level = {k: v for k, v in cfg.items() if k in _TOP_LEVEL_KEYS}
        extra_model_kwargs = {k: v for k, v in cfg.items() if k not in _TOP_LEVEL_KEYS}
        return init_chat_model(
            **top_level,
            model_kwargs=extra_model_kwargs | kwargs,
        )

    return init_chat_model(model=model, **_GENERIC_DEFAULTS, model_kwargs=kwargs)