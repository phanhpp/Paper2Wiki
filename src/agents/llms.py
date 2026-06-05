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

def set_up_llms(model, **kwargs):
    if model in MODEL_CONFIG:
        cfg = MODEL_CONFIG[model]
        top_level = {k: v for k, v in cfg.items() if k in _TOP_LEVEL_KEYS}
        extra_model_kwargs = {k: v for k, v in cfg.items() if k not in _TOP_LEVEL_KEYS}
        return init_chat_model(
            **top_level,
            model_kwargs=extra_model_kwargs | kwargs,
        )
    else:
        raise ValueError(f"Model {model} not found in MODEL_CONFIG")