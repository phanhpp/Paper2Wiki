"""Anomaly detection over a ``TraceReport``.

Two separate tools:

``compute_baselines_async(report)``
    Parses a TraceReport, computes per-name and per-flow baselines, and
    persists them to BASELINES_PATH. Intended to run on a schedule so
    baselines accumulate from a rolling window of real traffic.

``detect_anomalies_async(report)``
    Loads the persisted baselines file and flags runs in the report that
    exceed 3× their baseline on any dimension.

If no baselines file exists yet (first run), detect_anomalies_async falls
back to empty baselines — only hard_error signals will fire until
compute_baselines_async has been run at least once.

Anomaly signals:
- ``hard_error:<first line>``
- ``latency_spike:<actual>s_vs_median_<median>s``
- ``token_blowout:<actual>_vs_median_<median>``
- ``step_count_spike:<flow>:<actual>_vs_median_<median>``

LangSmith evaluation runs (``ls_experiment_id`` in metadata) are excluded.
"""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any
import statistics

from pydantic import BaseModel
from langchain.tools import tool

from src.tools.fetch_traces import TraceReport

MINIMUM_SAMPLES = 3
_SPIKE_MULTIPLIER = 3
_LLM_RUN_TYPES = {"llm"}
_EXCLUDE_NAMES = {"model", "tools", "ChatAnthropic"}
BASELINES_PATH = Path(__file__).resolve().parents[2] / "memories" / "baselines.json"

_RUN_LINE_RE = re.compile(r"^\[depth=\d+\] (\{.+\})\s*$")


class AnomalySignal(BaseModel):
    run_name: str
    trace_id: str
    flow: str | None
    signals: list[str]


class AnomalyReport(BaseModel):
    total_runs_analyzed: int
    anomalous_run_count: int
    anomalies: list[AnomalySignal]


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _load_traces(report: TraceReport) -> dict[str, str]:
    """Return ``{trace_id: formatted_string}`` from a TraceReport, reading from disk if offloaded."""
    if not report.is_offloaded:
        return report.traces  # type: ignore[return-value]
    with open(report.traces_path, encoding="utf-8") as f:  # type: ignore[arg-type]
        return json.load(f)


def _parse_runs(traces: dict[str, str]) -> list[dict[str, Any]]:
    """Parse formatted trace strings back into slim run dicts.

    Each ``[depth=N] {slim_dict}`` line is a valid Python dict literal
    produced by ``_slim()``. Non-dict lines are skipped.
    """
    runs: list[dict[str, Any]] = []
    for trace_id, text in traces.items():
        for line in text.splitlines():
            m = _RUN_LINE_RE.match(line.strip())
            if not m:
                continue
            try:
                slim = ast.literal_eval(m.group(1))
                if isinstance(slim, dict):
                    slim["trace_id"] = trace_id
                    runs.append(slim)
            except (ValueError, SyntaxError):
                pass
    return runs


def _flow(run: dict) -> str | None:
    """Return the flow label from run metadata, or None if absent. Runs without a flow are excluded from baselines and step-count tracking."""
    return (run.get("metadata") or {}).get("flow")


def _is_eval_run(run: dict) -> bool:
    """Return True if the run belongs to a LangSmith evaluation experiment and should be skipped."""
    return "ls_experiment_id" in (run.get("metadata") or {})


# ---------------------------------------------------------------------------
# compute_baselines_async  (scheduled maintenance tool)
# ---------------------------------------------------------------------------

@tool
async def compute_baselines_async(report: TraceReport) -> dict:
    """Compute per-name and per-flow baselines from a TraceReport and persist them.

    Intended to run on a schedule (e.g. daily) so baselines reflect a rolling
    window of real traffic. Overwrites BASELINES_PATH each run.

    Baselines computed:
    - by_name: median latency + median tokens per run name (llm runs only for tokens)
    - by_flow: median step count per flow label, where step count = number of
      completed runs (latency not None) sharing the same flow within one trace

    Args:
        report: From ``run_trace_report_async()``.

    Returns:
        The baselines dict that was written to disk.
    """
    traces = _load_traces(report)
    runs = _parse_runs(traces)

    # per run-name: collect latency samples (all runs) and token samples (llm runs only)
    name_groups: dict[str, dict[str, list]] = defaultdict(
        lambda: {"latencies": [], "tokens": []}
    )
    # per trace: count completed runs (latency not None) per flow label
    # shape: {trace_id: {flow: count}} — used to derive step-count baseline
    trace_flow_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for r in runs:
        if _is_eval_run(r):
            continue
        name = r.get("name")
        # skip LangGraph internals — their latency spans the whole graph, not a single operation
        if name in _EXCLUDE_NAMES:
            continue
        latency = r.get("latency")
        if latency is not None:
            name_groups[name]["latencies"].append(latency)
            # count this completed run toward its trace+flow bucket for step-count baseline
            trace_id = r.get("trace_id", "")
            flow = _flow(r)
            if trace_id and flow:
                trace_flow_counts[trace_id][flow] += 1
        # tokens are only meaningful on llm runs; tool/chain runs always report 0
        if r.get("run_type") in _LLM_RUN_TYPES:
            tokens = r.get("total_tokens")
            if tokens:
                name_groups[name]["tokens"].append(tokens)

    # emit a baseline entry only when we have enough samples to trust the median
    by_name: dict[str, dict] = {}
    for name, data in name_groups.items():
        lats, toks = data["latencies"], data["tokens"]
        sample_count = len(lats)
        if sample_count >= MINIMUM_SAMPLES:
            by_name[name] = {
                "median_latency": statistics.median(lats),
                "median_tokens":  statistics.median(toks) if toks else None,
                "sample_count":   sample_count,
            }

    # invert trace_flow_counts: group per-trace step counts by flow across all traces
    # so we can compute the median number of steps a given flow normally takes per trace
    flow_step_lists: dict[str, list[int]] = defaultdict(list)
    for flow_map in trace_flow_counts.values():
        for flow, count in flow_map.items():
            flow_step_lists[flow].append(count)

    by_flow: dict[str, dict] = {}
    for flow, counts in flow_step_lists.items():
        by_flow[flow] = {
            # None when fewer than MINIMUM_SAMPLES traces observed for this flow
            "median_steps": statistics.median(counts) if len(counts) >= MINIMUM_SAMPLES else None,
            "sample_count": len(counts),
        }

    baselines = {"by_name": by_name, "by_flow": by_flow}
    BASELINES_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINES_PATH.write_text(json.dumps(baselines, indent=2))
    return baselines


# ---------------------------------------------------------------------------
# detect_anomalies_async  (called during trace analysis)
# ---------------------------------------------------------------------------

def _load_baselines() -> dict:
    """Load persisted baselines from disk, returning empty dicts if the file does not exist yet."""
    if not BASELINES_PATH.exists():
        return {"by_name": {}, "by_flow": {}}
    return json.loads(BASELINES_PATH.read_text())


def _is_failure(run: dict, baselines: dict, trace_flow_counts: dict) -> tuple[bool, list[str]]:
    """Check a single slim run dict for anomaly signals against pre-loaded baselines.

    Args:
        run:               Slim run dict from ``_parse_runs()``.
        baselines:         Loaded from ``_load_baselines()``.
        trace_flow_counts: ``{trace_id: {flow: completed_run_count}}`` for the current report,
                           used to evaluate step-count spikes.

    Returns:
        ``(is_anomalous, signals)`` — signals is empty when the run is clean.
    """
    if _is_eval_run(run):
        return False, []

    signals: list[str] = []
    name = run.get("name", "unknown")
    # None when this run name has no baseline yet (fewer than MINIMUM_SAMPLES seen)
    by_name = baselines["by_name"].get(name, {})

    # always flag hard errors regardless of baselines
    error = run.get("error")
    if error:
        signals.append(f"hard_error:{str(error).splitlines()[0][:80]}")

    # latency spike: skip if no baseline exists for this run name
    latency = run.get("latency")
    median_latency = by_name.get("median_latency")
    if latency is not None and median_latency is not None:
        if latency > _SPIKE_MULTIPLIER * median_latency:
            signals.append(f"latency_spike:{latency:.1f}s_vs_median_{median_latency:.1f}s")

    # token blowout: only fires for llm runs; tool runs always report 0 tokens
    tokens = run.get("total_tokens")
    median_tokens = by_name.get("median_tokens")
    if tokens and median_tokens is not None:
        if tokens > _SPIKE_MULTIPLIER * median_tokens:
            signals.append(f"token_blowout:{tokens}_vs_median_{median_tokens:.0f}")

    # step count spike: compare how many runs this trace logged for the same flow
    # against the baseline median — catches agent loops that ran far longer than usual
    flow = _flow(run)
    trace_id = run.get("trace_id", "")
    if flow and trace_id:
        median_steps = baselines["by_flow"].get(flow, {}).get("median_steps")
        if median_steps is not None:
            actual = trace_flow_counts.get(trace_id, {}).get(flow, 0)
            if actual > _SPIKE_MULTIPLIER * median_steps:
                signals.append(f"step_count_spike:{flow}:{actual}_vs_median_{median_steps:.0f}")

    return bool(signals), signals


@tool
async def detect_anomalies_async(report: TraceReport) -> AnomalyReport:
    """Detect anomalous runs in a TraceReport using persisted baselines.

    Loads baselines from the file written by ``compute_baselines_async``.
    If no baselines file exists yet, only hard_error signals will fire.

    Args:
        report: From ``run_trace_report_async()``.

    Returns:
        AnomalyReport with total_runs_analyzed, anomalous_run_count, and a
        list of AnomalySignal (run_name, trace_id, flow, signals).
    """
    traces = _load_traces(report)
    runs = _parse_runs(traces)
    # baselines were computed offline by compute_baselines_async; may be empty on first run
    baselines = _load_baselines()

    # build per-trace flow step counts for the current report so _is_failure can
    # compare each run's trace against the baseline median_steps for its flow
    trace_flow_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in runs:
        if _is_eval_run(r) or r.get("latency") is None:
            continue
        trace_id = r.get("trace_id", "")
        flow = _flow(r)
        if trace_id and flow:
            trace_flow_counts[trace_id][flow] += 1

    anomalies: list[AnomalySignal] = []
    for r in runs:
        failed, signals = _is_failure(r, baselines, trace_flow_counts)
        if failed:
            anomalies.append(AnomalySignal(
                run_name=r.get("name", "unknown"),
                trace_id=r.get("trace_id", ""),
                flow=_flow(r),
                signals=signals,
            ))

    return AnomalyReport(
        total_runs_analyzed=len(runs),
        anomalous_run_count=len(anomalies),
        anomalies=anomalies,
    )
