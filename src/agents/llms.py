from langchain_anthropic import ChatAnthropic

HAIKU_MODEL = "claude-haiku-4-5-20251001"
SONNET_MODEL = "claude-sonnet-4-6"

# Reusable model singletons
haiku_llm = ChatAnthropic(
    model=HAIKU_MODEL,
    max_retries=8,
    timeout=120.0,
)

expensive_llm = ChatAnthropic(
    model=SONNET_MODEL,
    max_retries=8,
    timeout=120.0,
    effort="medium",
    thinking={"type": "adaptive"},
    max_tokens=8000,
)
