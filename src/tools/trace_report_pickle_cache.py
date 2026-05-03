"""Dev-only pickle cache helpers for trace_report.

Pickle is useful while iterating locally because it can preserve LangSmith Run
objects without repeatedly fetching them. Do not use this module in production:
pickle files are not stable across dependency changes and are unsafe to load
from untrusted sources.
"""

from __future__ import annotations

import json
import pickle
from pathlib import Path
from typing import Any

from langsmith import Client

from src.tools.fetch_traces import TRACE_OFFLOAD_DIR, TraceReport, _build_trace_report_async


def save_runs(runs: list[Any], cache_path: Path) -> None:
    """Pickle runs to disk for offline local testing."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("wb") as f:
        pickle.dump(runs, f)
    print(f"Saved {len(runs)} runs to {cache_path}")


def load_runs(cache_path: Path) -> list[Any]:
    """Load pickled runs from disk."""
    with cache_path.open("rb") as f:
        runs = pickle.load(f)
    print(f"Loaded {len(runs)} runs from {cache_path}")
    return runs


def run_from_cache(cache_path: Path | str, offload: bool = False) -> TraceReport:
    """Load runs from a pickle cache and return a TraceReport without fetching."""
    client = Client()
    cache_path = Path(cache_path)
    runs = load_runs(cache_path)
    traces = _build_trace_report_async(runs, client)
    error_count = sum(1 for r in runs if r.error)
    total_cost = sum(float(r.total_cost or 0) for r in runs)

    report: TraceReport = {
        "project": "(from pickle cache)",
        "fetched_at": "(from pickle cache)",
        "start_time": "(from pickle cache)",
        "end_time": "(from pickle cache)",
        "run_count": len(runs),
        "trace_count": len(traces),
        "error_count": error_count,
        "total_cost": total_cost,
    }

    if offload:
        traces_path = TRACE_OFFLOAD_DIR / f"{cache_path.stem}.traces.json"
        traces_path.parent.mkdir(parents=True, exist_ok=True)
        traces_path.write_text(json.dumps(traces, ensure_ascii=False, indent=2))
        report["traces_path"] = str(traces_path)
    else:
        report["traces"] = traces

    return report
