"""Deterministic eval gate — reads eval/pr_gate_cases.json, calls each tool directly.

This script bypasses the agent entirely. It imports tools from src/ and calls
tool.ainvoke(inputs) directly, so it tests tool logic in isolation — not agent
routing, not skill compliance.

Two case types (set via "type" field in pr_gate_cases.json):
  - "regression": - what the agent already does well - must hold 100% pass rate. Any drop blocks the merge.
  - "capability": - what the agent can do but not yet 100% - tracked in results.json but never gate-blocking. Promote to
    regression once the case holds 100% across 3+ consecutive runs.

Each case supports one expect_* assertion (see pr_gate_cases.json for field schema).
Cases with "requires_web_provider": true are silently skipped when no provider
API key is configured — they run locally or in CI jobs that have secrets.

Run:
    uv run python eval/run_gate.py

Writes eval/results.json. Exits 1 if any regression category scores below 100%
(REGRESSION_THRESHOLDS). Capability categories are tracked/printed but never block — when a
capability case reliably passes, promote it (change "type" → "regression" in pr_gate_cases.json)
to lock it into the hard gate. Promotion is the "lock in a gain" mechanism; there is no separate
baseline file.
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

# Add repo root to sys.path so `import src` works when the script is run directly
# (pytest adds it automatically; plain `python eval/run_gate.py` does not).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 1.00 = every regression case in that category must pass.
# These are deterministic checks — there is no "sometimes passes."
# If arXiv flakiness causes retrieval failures repeatedly, demote those
# cases to "capability" type rather than lowering this threshold.
# NOTE: only categories listed here are gate-checked. A regression case in an unlisted
# category is scored but silently never blocks — keep this in sync with pr_gate_cases.json.
REGRESSION_THRESHOLDS = {
    "retrieval": 1.00,
    "health":    1.00,
    "boundary":  1.00,
    "hashing":   1.00,
}

# Checked once at startup — web_search/web_extract cases are skipped entirely
# when no provider key is set (avoids RuntimeError: "No search provider available").
_HAS_WEB_PROVIDER = any(
    os.getenv(k) for k in ["FIRECRAWL_API_KEY", "TAVILY_API_KEY", "EXA_API_KEY"]
)


def _load_tool_map() -> dict:
    """Import all tools directly (mode-independent).

    Importing here rather than via src.tools.all_tools so that fetch_arxiv
    and web tools are always available regardless of config ingest mode.
    """
    from src.tools.wiki_integrity_check import quick_wiki_integrity_check
    from src.tools.web_tools.tools import web_search, web_extract
    from src.tools.arxiv_tool import fetch_arxiv
    from src.tools.hash_tools import compute_sha256
    tools = [quick_wiki_integrity_check, web_search, web_extract, fetch_arxiv, compute_sha256]
    return {t.name: t for t in tools}


async def run_case(case: dict, tool_map: dict) -> dict:
    """Run a single eval case and return a result dict.

    Result fields:
      id, type, passed (bool), duration_ms
      skipped (bool, optional) — set when case is skipped, not failed
      reason (str, optional)  — set when passed=False or skipped=True
    """
    case_id   = case["id"]
    case_type = case.get("type", "regression")

    # Skip web tool cases when no API key is available.
    # "skipped" cases are excluded from scoring — they don't count as failures.
    if case.get("requires_web_provider") and not _HAS_WEB_PROVIDER:
        return {"id": case_id, "type": case_type, "passed": True,
                "skipped": True, "reason": "no web provider configured", "duration_ms": 0}

    tool = tool_map.get(case["tool"])
    if tool is None:
        # This should not happen — all tools in pr_gate_cases.json must be in _load_tool_map().
        return {"id": case_id, "type": case_type, "passed": False,
                "reason": f"tool {case['tool']!r} not found — add it to _load_tool_map()", "duration_ms": 0}

    t0 = time.monotonic()
    try:
        result = await tool.ainvoke(case["inputs"])
        duration_ms = int((time.monotonic() - t0) * 1000)

        # expect_empty: tool must return a falsy value ([], {}, "", None).
        # Used for edge cases like web_extract([]) which returns [] immediately.
        if case.get("expect_empty"):
            if result:
                return {"id": case_id, "type": case_type, "passed": False,
                        "reason": f"expected empty, got: {result!r}", "duration_ms": duration_ms}
            return {"id": case_id, "type": case_type, "passed": True, "duration_ms": duration_ms}

        # expect_keys: each string must appear as a substring in the JSON-serialized output.
        # Works for dict keys, list field names, and plain string prefixes (e.g. "wiki-check").
        if "expect_keys" in case:
            result_str = json.dumps(result, default=str).lower()
            missing = [k for k in case["expect_keys"] if k.lower() not in result_str]
            if missing:
                return {"id": case_id, "type": case_type, "passed": False,
                        "reason": f"missing keys: {missing}", "duration_ms": duration_ms}

        # expect_keys_or: pass if ANY of the provided key-sets all appear in the output.
        # Use when a tool can return multiple valid structured responses (e.g. paper fields
        # on success, {"error": "rate_limited"} when arXiv throttles).
        if "expect_keys_or" in case:
            result_str = json.dumps(result, default=str).lower()
            matched = any(
                all(k.lower() in result_str for k in key_set)
                for key_set in case["expect_keys_or"]
            )
            if not matched:
                return {"id": case_id, "type": case_type, "passed": False,
                        "reason": f"no key-set matched: {case['expect_keys_or']}", "duration_ms": duration_ms}

        # expect_value: result must be one of the listed values (exact match).
        if "expect_value" in case and result not in case["expect_value"]:
            return {"id": case_id, "type": case_type, "passed": False,
                    "reason": f"got {result!r}", "duration_ms": duration_ms}

        return {"id": case_id, "type": case_type, "passed": True, "duration_ms": duration_ms}

    except Exception as e:
        duration_ms = int((time.monotonic() - t0) * 1000)

        # expect_error: pass if the tool raised any exception.
        # Use for inputs that should always fail (e.g. nonexistent arXiv ID).
        if case.get("expect_error"):
            return {"id": case_id, "type": case_type, "passed": True, "duration_ms": duration_ms}

        # expect_error_contains: pass only if the exception message contains the string.
        # More precise than expect_error — use for SSRF / boundary checks where the
        # error message itself is the contract (e.g. "blocked").
        if "expect_error_contains" in case:
            if case["expect_error_contains"].lower() in str(e).lower():
                return {"id": case_id, "type": case_type, "passed": True, "duration_ms": duration_ms}

        return {"id": case_id, "type": case_type, "passed": False,
                "reason": str(e)[:300], "duration_ms": duration_ms}


async def main():
    cases   = json.loads(Path("eval/pr_gate_cases.json").read_text())

    tool_map = _load_tool_map()

    # Run all cases concurrently — tools are independent, order doesn't matter.
    results = await asyncio.gather(*[run_case(c, tool_map) for c in cases])

    # Split into active (scored) vs skipped (excluded from gate).
    active    = [(r, c) for r, c in zip(results, cases) if not r.get("skipped")]
    skipped   = [(r, c) for r, c in zip(results, cases) if r.get("skipped")]
    reg_pairs = [(r, c) for r, c in active if c.get("type") == "regression"]
    cap_pairs = [(r, c) for r, c in active if c.get("type") == "capability"]

    def category_scores(pairs: list) -> dict[str, float]:
        """Pass rate per category: {category: 0.0–1.0}."""
        by_cat: dict[str, list[bool]] = {}
        for r, c in pairs:
            by_cat.setdefault(c["category"], []).append(r["passed"])
        return {cat: sum(v) / len(v) for cat, v in by_cat.items()}

    reg_scores = category_scores(reg_pairs)
    cap_scores = category_scores(cap_pairs)
    overall    = (
        sum(r["passed"] for r in results if not r.get("skipped")) / len(active)
        if active else 1.0
    )

    # Only regression categories block the gate (must score 100%). Capability categories are
    # tracked/printed but never block — when one is reliably passing, promote it to regression
    # (change its "type" in pr_gate_cases.json) to lock it into the hard gate.
    gate_failures = []
    for cat, threshold in REGRESSION_THRESHOLDS.items():
        score = reg_scores.get(cat, 1.0)
        if score < threshold:
            gate_failures.append(f"regression/{cat}: {score:.0%} < {threshold:.0%}")

    case_failures = [
        f"{r['id']} ({r.get('type', '?')}): {r.get('reason', '')}"
        for r in results
        if not r["passed"] and not r.get("skipped")
    ]

    output = {
        "gate_passed":         len(gate_failures) == 0,
        "overall":             overall,
        "regression_scores":   reg_scores,
        "capability_scores":   cap_scores,   # tracked only, never gate-blocking
        "gate_failures":       gate_failures,
        "case_failures":       case_failures,
        "passed":              sum(r["passed"] for r in results if not r.get("skipped")),
        "skipped":             len(skipped),
        "total":               len(active),
        "timings_ms":          {r["id"]: r["duration_ms"] for r in results if not r.get("skipped")},
    }

    Path("eval/results.json").write_text(json.dumps(output, indent=2))
    print(json.dumps(output, indent=2))

    # Nudge: a capability category at 100% is a candidate to PROMOTE to regression (lock it into
    # the hard gate). Printed to stdout and surfaced on the PR via GitHub's step summary.
    nudges = [
        f"📈 capability/{cat} = {score:.0%} — promote its cases to regression once reliably passing"
        for cat, score in sorted(cap_scores.items()) if score >= 1.0
    ]
    if nudges:
        print("\n".join(nudges))
    _write_step_summary(cap_scores, nudges)

    if not output["gate_passed"]:
        sys.exit(1)


def _write_step_summary(cap_scores: dict, nudges: list[str]) -> None:
    """Append capability scores (tracked, non-blocking) + promotion nudges to the PR checks summary.

    No-op outside GitHub Actions (GITHUB_STEP_SUMMARY unset), so local runs are unaffected.
    """
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path or not cap_scores:
        return
    lines = ["### Capability scores (tracked, non-blocking)", "", "| category | score |", "|---|---|"]
    for cat, score in sorted(cap_scores.items()):
        lines.append(f"| {cat} | {score:.0%} |")
    if nudges:
        lines += ["", *nudges]
    with open(summary_path, "a") as f:
        f.write("\n".join(lines) + "\n")


asyncio.run(main())
