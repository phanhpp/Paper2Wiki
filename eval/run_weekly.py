"""Weekly CI pipeline — fetch live traces and refresh baselines.

Intentionally limited to two steps:
    1. Fetch last N days of traces from LangSmith
    2. compute_baselines_async  — update rolling per-run-name medians in
       memories/baselines.json so detect_anomalies_async has a fresh 3x threshold

Steps 3-4 (detect anomalies -> push to LangSmith datasets) are HITL-only.
They run via the trace-analysis skill, where a human reviews the anomaly report
before any dataset write happens. Pushing datasets automatically risks committing
infrastructure noise (chain-level OOM / network timeouts) as regression examples.

The weekly CI gate is pytest -m langsmith — it replays existing hard_error examples
from LangSmith datasets that were already human-reviewed and approved.

Run:
    uv run --env-file .env python eval/run_weekly.py
    uv run --env-file .env python eval/run_weekly.py --days 14 --limit 200
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Add repo root to sys.path so `import src` works when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.tools.observability_eval_tools.fetch_traces import run_trace_report_async
from src.tools.observability_eval_tools.anomaly_detection import compute_baselines_async

# Use .coroutine to call the underlying async function directly —
# bypasses LangChain tool validation overhead, appropriate for a script.
_fetch     = run_trace_report_async.coroutine
_baselines = compute_baselines_async.coroutine


async def run(project: str, days: int, limit: int) -> int:
    """Run the baseline-refresh pipeline. Returns exit code (0 = success, 1 = fatal error)."""

    # -- 1. Fetch traces -------------------------------------------------------
    print(f"[weekly] Fetching traces: project={project!r} days={days} limit={limit}")
    report = await _fetch(project=project, days=days, limit=limit)

    print(f"[weekly] Fetched {report.trace_count} traces (offloaded={report.is_offloaded})")

    if not report.trace_count:
        print("[weekly] No traces found — skipping baseline update.")
        return 0

    # -- 2. Update baselines ---------------------------------------------------
    # Overwrites memories/baselines.json with fresh per-run-name medians.
    # detect_anomalies_async uses these as the 3x spike threshold — running
    # weekly keeps baselines from drifting as traffic patterns change.
    print("[weekly] Updating baselines...")
    baseline_result = await _baselines(report=report)
    n_updated = len(baseline_result.get("by_name", {}))
    print(f"[weekly] Baselines updated: {n_updated} run-name entries")

    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default="paper2wiki", help="LangSmith project name")
    p.add_argument("--days",    type=int, default=7,   help="Lookback window in days")
    p.add_argument("--limit",   type=int, default=100, help="Max traces to fetch")
    return p.parse_args()


if __name__ == "__main__":
    args = parse_args()
    sys.exit(asyncio.run(run(project=args.project, days=args.days, limit=args.limit)))
