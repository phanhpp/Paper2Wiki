"""Shared helpers for golden evaluators and run_weekly_eval."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel

from src.ingest_mode import get_ingest_mode


def _judge():
    """The judge model for the configured provider.

    Resolved through ``get_model_spec("judge")`` like every other auxiliary task, so a
    non-Anthropic base model (or an ``auxiliary.judge`` override) is honoured here too.
    Imported lazily so importing this module doesn't pull in the agent stack.
    """
    from src.agents.llms import set_up_llms
    from src.llm_roles import get_model_spec

    return set_up_llms(get_model_spec("judge"))


def call_matches(expected: str | dict, actual: dict) -> bool:
    """Return True if an actual trajectory call matches an expected call spec."""
    if isinstance(expected, str):
        return actual["name"] == expected

    if actual["name"] != expected["name"]:
        return False

    args_contains = expected.get("args_contains")
    if args_contains and args_contains not in str(actual.get("args", "")):
        return False

    return True


def message_text(content) -> str:
    """Normalize agent message content (str or Anthropic-style block list) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content)


def compact_text(text: str) -> str:
    """Collapse repeated whitespace to save judge context without changing words."""
    return re.sub(r"\s+", " ", text).strip()


class _JudgeOutput(BaseModel):
    score: int
    reason: str


class _MultiJudgeOutput(BaseModel):
    scores: dict[str, Any]


def llm_judge(system: str, user_content: str, key: str, max_input_chars: int = 12000) -> dict:
    """Call the Sonnet judge with structured output; return a LangSmith result dict."""
    compacted_content = compact_text(user_content)
    try:
        result = _judge().with_structured_output(_JudgeOutput).invoke(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": compacted_content[:max_input_chars]},
            ]
        )
        return {
            "key": key,
            "score": float(result.score),
            "comment": result.reason,
        }
    except Exception as exc:
        return {"key": key, "score": 0.0, "comment": f"judge error: {str(exc)[:120]}"}


def llm_judge_multi(rubric: str, answer: str, keys: list[str], max_input_chars: int = 12000) -> list[dict]:
    """Call the Sonnet judge with a multi-dimension rubric; return one LangSmith result dict per key."""
    from pydantic import create_model as _create_model, Field as _Field
    compacted = compact_text(answer)

    # Required fields (no default) so the model must return them — optional fields default to 0.
    field_defs: dict = {k: (int, _Field(..., description="0 or 1")) for k in keys}
    field_defs.update({f"{k}_reason": (str, _Field(...)) for k in keys})
    _Output = _create_model("_MultiJudgeOutput", **field_defs)

    try:
        parsed = _judge().with_structured_output(_Output).invoke(
            [{"role": "user", "content": (rubric + "\n\nAnswer to evaluate:\n" + compacted)[:max_input_chars]}]
        )
        scores = parsed.model_dump()
    except Exception as exc:
        preview = str(exc)[:120]
        return [{"key": k, "score": 0.0, "comment": f"judge error: {preview}"} for k in keys]

    results = []
    for key in keys:
        score = scores.get(key, 0.0)
        reason = scores.get(f"{key}_reason", scores.get("reason", ""))
        try:
            score = float(bool(score) if isinstance(score, bool) else score)
        except (TypeError, ValueError):
            score = 0.0
        results.append({"key": key, "score": score, "comment": str(reason)})
    return results
