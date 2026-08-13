"""Turn failed checks into a retry message.

Feedback quality is the whole reason a retry is worth more than a plain re-run:
name the specific gap ("graph.json has no node for 'attention'"), never restate
the rule ("the graph must be consistent").
"""

from __future__ import annotations

from src.middleware.types import CheckResult, RunContext

SKILL_PATH = "skills/llm-wiki/SKILL.md"

_HEADER = (
    "Your work did not pass verification. Fix the following, then finish the task.\n"
    "Do not start over — correct what is listed."
)

_SKILL_HINT = (
    f"You did not read `{SKILL_PATH}` during this run. Read it first — it "
    "documents the conventions these checks enforce."
)


def _skill_hint_applies(ctx: RunContext) -> bool:
    """Should we tell the agent to go read the llm-wiki skill?

    Only when this was an ingest run AND it never opened the skill.

    The skill explains how to write wiki pages, so on a failed ingest, not
    reading it is a likely reason. On a failed query it has nothing to do with
    the problem — and since the hint prints first, adding it would bury the real
    failure under something irrelevant.
    """
    if "ingest" not in ctx.paths:
        return False
    # Substring match: tool args carry virtual paths (/skills/...), the constant is bare.
    return not any(SKILL_PATH in r for r in ctx.reads)


def feedback_text(failed: list[CheckResult], ctx: RunContext) -> str:
    """The retry message body.

    When the checks failed *and* the skill was never read, the missing skill is
    the likely cause, so it leads — fixing the symptom without it tends to
    reproduce the same gap on the next attempt.
    """
    lines = [_HEADER, ""]

    if _skill_hint_applies(ctx):
        lines += [_SKILL_HINT, ""]

    for result in failed:
        lines.append(f"- [{result.id}] {result.gap}")

    return "\n".join(lines)


def explain(results: list[CheckResult]) -> str:
    """One-line summary for ``Evaluation.explanation``."""
    failed = [r for r in results if not r.passed]
    if not failed:
        return f"all {len(results)} checks passed"
    ids = ", ".join(r.id for r in failed)
    return f"{len(failed)} of {len(results)} checks failed: {ids}"
