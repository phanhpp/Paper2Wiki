"""Shared helpers for golden evaluators and run_weekly_eval."""

from __future__ import annotations

import json
import re

import anthropic

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


def parse_judge_json(text: str) -> dict:
    """Parse judge JSON from model output; tolerate markdown fences and extra prose."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text.strip())

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


def judge_score(result: dict) -> float:
    score = result.get("score")
    if isinstance(score, bool):
        return float(score)
    return float(score)


def llm_judge(system: str, user_content: str, key: str, max_input_chars: int = 12000) -> dict:
    """Call the Sonnet judge; return a LangSmith result dict."""
    compacted_content = compact_text(user_content)
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=JUDGE_MODEL,
        max_tokens=256,
        system=system,
        messages=[{"role": "user", "content": compacted_content[:max_input_chars]}],
    )
    raw = resp.content[0].text
    try:
        result = parse_judge_json(raw)
        return {
            "key": key,
            "score": judge_score(result),
            "comment": result.get("reason") or result.get("comment") or "",
        }
    except Exception:
        preview = raw.replace("\n", " ")[:120]
        return {"key": key, "score": 0.0, "comment": f"parse error: {preview!r}"}
