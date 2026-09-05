"""
Small, dependency-light helpers shared across tool modules.

This module is intentionally tiny (stdlib-only) so it can be imported from
anywhere without creating dependency cycles.
"""

from __future__ import annotations

import os
import re
from difflib import SequenceMatcher
from pathlib import Path

from src.paths import user_root


def norm_title(s: str) -> str:
    """Normalize a paper title or query for fuzzy comparison."""
    s = s.lower().strip()
    s = re.sub(r"\s+", " ", s)
    # Drop punctuation while keeping alphanumerics/spaces.
    s = re.sub(r"[^a-z0-9 ]+", "", s)
    return s


def title_score(query: str, candidate_title: str) -> float:
    """
    Score how well `candidate_title` matches `query`.

    Higher is better. Exact (normalized) title matches dominate; otherwise we use
    a mix of token overlap and a sequence similarity ratio.
    """
    q = norm_title(query)
    t = norm_title(candidate_title)
    if not q or not t:
        return 0.0
    if q == t:
        return 10_000.0

    q_tokens = set(q.split())
    t_tokens = set(t.split())
    if not q_tokens:
        return 0.0

    overlap = len(q_tokens & t_tokens) / len(q_tokens)
    ratio = SequenceMatcher(a=q, b=t).ratio()

    # Prefer candidates that contain the whole query as a substring.
    contains_bonus = 0.15 if q in t else 0.0

    return 100.0 * (0.65 * ratio + 0.35 * overlap + contains_bonus)


def get_repo_root() -> Path:
    """Root for user data — the wiki, and anything else the user owns.

    Kept under this name because callers read it as "where our files are", but it is
    `user_root()`: the repo when cloned, `~/.any2wiki` when installed. Never `__file__`,
    which would put user data inside site-packages.
    """
    return user_root()


def get_wiki_root() -> Path:
    """
    Resolve the wiki vault root directory.

    Priority:
    - `WIKI_PATH` environment variable (supports `~`)
    - `<repo_root>/wiki`
    """
    return Path(os.environ.get("WIKI_PATH", get_repo_root() / "wiki")).expanduser().resolve()


# Tool names that belong to the trace-analysis pipeline itself.
# Used in two places:
#   - fetch_traces._format_trace_async: skips fetching full I/O for these tools
#     (their inputs/outputs are large blobs that add no diagnostic value).
#   - anomaly_detection._parse_runs / detect_anomalies_async: skips anomaly
#     checks on these runs to avoid false positives from self-analysis calls.
TRACE_ANALYSIS_TOOLS: frozenset[str] = frozenset({
    "detect_anomalies_async",
    "run_trace_report_async",
    "summarize_traces_async",
    "compute_baselines_async",
    "fetch_traces",
})

