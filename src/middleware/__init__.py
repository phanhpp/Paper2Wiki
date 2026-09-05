"""Loop 2 — in-run verification middleware for Any2Wiki.

See ``README.md`` in this package for what is checked and how to add a check,
for the design rationale.
"""

from src.middleware.types import CheckResult, Evaluation, RunContext
from src.middleware.wiki_rubric import WikiRubricMiddleware, WikiRubricState

__all__ = [
    "CheckResult",
    "Evaluation",
    "RunContext",
    "WikiRubricMiddleware",
    "WikiRubricState",
]
