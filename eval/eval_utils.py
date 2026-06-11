"""Shared helpers for golden evaluators and run_weekly_eval."""

from __future__ import annotations

import re
from typing import Any

import anthropic
from pydantic import BaseModel

from src.ingest_mode import get_ingest_mode

JUDGE_MODEL = "claude-sonnet-4-6"


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
    client = anthropic.Anthropic()
    try:
        resp = client.messages.parse(
            model=JUDGE_MODEL,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": compacted_content[:max_input_chars]}],
            output_format=_JudgeOutput,
        )
        result = resp.parsed_output
        return {
            "key": key,
            "score": float(result.score),
            "comment": result.reason,
        }
    except Exception as exc:
        return {"key": key, "score": 0.0, "comment": f"judge error: {str(exc)[:120]}"}


def llm_judge_multi(rubric: str, answer: str, keys: list[str], max_input_chars: int = 12000) -> list[dict]:
    """Call the Sonnet judge with a multi-dimension rubric; return one LangSmith result dict per key."""
    compacted = compact_text(answer)

    fields_desc = ", ".join(f'"{k}": <0 or 1>, "{k}_reason": "<reason>"' for k in keys)
    system = (
        f"You are an evaluator. Score each dimension 0 or 1.\n"
        f"Return a JSON object with these exact keys: {{{fields_desc}}}.\n"
        "No markdown, no extra keys."
    )

    client = anthropic.Anthropic()

    class _DynamicOutput(BaseModel):
        model_config = {"extra": "allow"}

    try:
        resp = client.messages.parse(
            model=JUDGE_MODEL,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": (rubric + "\n\nAnswer to evaluate:\n" + compacted)[:max_input_chars]}],
            output_format=_DynamicOutput,
        )
        scores = resp.parsed_output.model_dump()
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
