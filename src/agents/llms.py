# from langchain_anthropic import ChatAnthropic
from langchain.chat_models import init_chat_model
from dotenv import load_dotenv

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
        "effort": "medium",
        "thinking": {"type": "adaptive"},
        "max_tokens": 8000,
    },
}


def set_up_llms(model, **kwargs):
    if model in MODEL_CONFIG:
        return init_chat_model(
            model=MODEL_CONFIG[model]["model"],
            max_retries=MODEL_CONFIG[model]["max_retries"],
            timeout=MODEL_CONFIG[model]["timeout"],
            max_tokens=MODEL_CONFIG[model]["max_tokens"],
            model_kwargs={
                k: v for k, v in MODEL_CONFIG[model].items() 
                if k not in ["model", "max_retries", "timeout", "max_tokens"]
            } | kwargs
        )
    else:
        raise ValueError(f"Model {model} not found in MODEL_CONFIG")