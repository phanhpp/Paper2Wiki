"""Check registry, keyed by contract.

Each check is a ``Check`` — ``RunContext -> CheckResult``. Adding a criterion is
appending a function here; the middleware runs ``CHECKS[path]`` without knowing
what any individual check does.

``query`` and ``marp`` land in later steps of the build order; the registry
returns an empty list for a path with no checks yet, so classification can
already route to them without the middleware changing.
"""

from __future__ import annotations

from src.middleware.checks import ingest, marp, query
from src.middleware.types import Check

CHECKS: dict[str, list[Check]] = {
    "ingest": ingest.CHECKS,
    "query": query.CHECKS,
    "marp": marp.CHECKS,
}


def checks_for(path: str) -> list[Check]:
    """Checks registered for ``path`` — empty when the path is not implemented."""
    return CHECKS.get(path, [])
