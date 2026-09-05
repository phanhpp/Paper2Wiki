"""Exercise every model role against one provider, cheaply.

Each role is a different code path, so "the supervisor works" proves little:

    supervisor / subagent  → tool calling
    title / web_summarize  → plain completion
    summarize / judge      → with_structured_output  ← the one that breaks per provider

Usage:
    uv run --env-file .env python <this> openai:gpt-4o
    uv run --env-file .env python <this>                # whatever config.yaml resolves to
"""

import sys

from pydantic import BaseModel, Field

from src.env import load_env

load_env()

MODEL = sys.argv[1] if len(sys.argv) > 1 else None
if MODEL:
    import os

    os.environ["PAPER2WIKI_MODEL"] = MODEL
    # Level 1 beats auxiliary.<task>.model in config.yaml, which otherwise pins haiku
    # and would silently keep testing Anthropic.
    for role in ("SUPERVISOR", "SUBAGENT", "TITLE", "SUMMARIZE", "JUDGE", "WEB_SUMMARIZE"):
        os.environ[f"PAPER2WIKI_MODEL_{role}"] = MODEL

from src.agents.llms import set_up_llms          # noqa: E402
from src.llm_roles import VALID_ROLES, get_model_spec  # noqa: E402


class _Verdict(BaseModel):
    """Mirrors the shape the judge and summarizer actually ask for."""

    score: int = Field(description="0 or 1")
    reason: str


def probe(role: str) -> tuple[str, str]:
    spec = get_model_spec(role)
    label = f"{spec.model}" + (f" via {spec.base_url}" if spec.base_url else "")
    try:
        llm = set_up_llms(spec)
    except Exception as exc:
        return "BUILD FAIL", f"{label} — {type(exc).__name__}: {str(exc)[:70]}"

    # Roles that need structured output are the ones that differ across providers.
    structured = role in {"summarize", "judge"}
    try:
        if structured:
            out = llm.with_structured_output(_Verdict).invoke(
                [{"role": "user", "content": "Score this 1 and say 'ok'."}]
            )
            assert isinstance(out.score, int), "score is not an int"
            detail = f"structured → score={out.score}"
        else:
            out = llm.invoke([{"role": "user", "content": "Reply with exactly: ok"}])
            text = out.content if isinstance(out.content, str) else str(out.content)
            detail = f"text → {text.strip()[:30]!r}"
        return "OK", f"{label} — {detail}"
    except Exception as exc:
        return "CALL FAIL", f"{label} — {type(exc).__name__}: {str(exc)[:90]}"


print(f"\nProbing roles with: {MODEL or '(config.yaml defaults)'}\n")
worst = 0
for role in sorted(VALID_ROLES):
    status, detail = probe(role)
    mark = {"OK": "  ok  ", "BUILD FAIL": " BUILD", "CALL FAIL": " CALL "}[status]
    tag = "  [structured]" if role in {"summarize", "judge"} else ""
    print(f"[{mark}] {role:14} {detail}{tag}")
    worst = max(worst, 0 if status == "OK" else 1)

print("\nAll roles OK." if not worst else "\nSome roles failed — see above.")
sys.exit(worst)
