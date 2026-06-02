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

Exclusion policy:
- LangSmith evaluation runs (``ls_experiment_id`` in metadata) are fully skipped.
- Spans with ``run_type == "chain"`` and ``name == "LangGraph"`` are fully
  skipped. This is the top-level LangGraph framework span — errors here are
  infra noise (state corruption, OOM), not user-code bugs. Other named chain
  nodes are custom graph nodes and are left through.
- LangGraph aggregate/internal span names ``model``, ``tools``, and
  ``ChatAnthropic`` are excluded from baseline computation only (hard errors
  on these names are still flagged by ``detect_anomalies_async``).
"""

from __future__ import annotations

import ast
import json
import re
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from langchain.tools import tool

from src.tools.observability_eval_tools.fetch_traces import TraceReport

MINIMUM_SAMPLES = 3
_SPIKE_MULTIPLIER = 3
_LLM_RUN_TYPES = {"llm"}
_EXCLUDE_NAMES = {"model", "tools", "ChatAnthropic"}
BASELINES_PATH = Path(__file__).resolve().parents[3] / "memories" / "baselines.json"

# Extracts the JSON string (including {}) from lines like: [depth=2] {"key": "value"}
_RUN_LINE_RE = re.compile(r"^\[depth=\d+\] (\{.+\})\s*$")
# Extracts whatever text comes AFTER the depth tag: [depth=1] some text
_TRACE_FRAME_RE = re.compile(r"^\[depth=\d+\]\s+(.+?)\s*$") 
TOOL_FLOWS = {
    "fetch_arxiv": "wiki-ingestion",
    "parse_pdf_docling": "wiki-ingestion",
    "quick_wiki_integrity_check": "wiki-health",
    "run_trace_report_async": "trace-analysis",
    "fetch_traces": "trace-analysis",
    "save_sandbox_output": "sandbox-dev",
    "get_sandbox_state": "sandbox-dev",
    "list_sandbox_files": "sandbox-dev",
}

AnomalyError = Literal["hard_error", "latency_spike", "token_blowout", "step_count_spike"]


class FailedSpan(BaseModel):
    id: str
    run_name: str
    run_type: str | None
    flow: str | None
    context_name: str | None = None
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    errors: list[AnomalyError]
    signals: list[str]


class AnomalySignal(BaseModel):
    trace_id: str
    errors: list[AnomalyError]  # deduplicated union across all failed spans
    signals: list[str]          # detailed anomaly strings across all failed spans
    failed_spans: list[FailedSpan]


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

    Each ``[depth=N] {slim_dict}`` line is a valid Python dict literal produced
    by ``_slim()`` (optionally extended with ``inputs``/``outputs`` for tool
    runs). 
    Non-dict trace-frame lines such as ``[depth=1] TodoListMiddleware.after_model`` are not emitted as runs, but
    the nearest preceding frame name is attached as ``context_name`` to the
    next parsed run. This keeps middleware context available for wrapper spans
    named only ``model`` or ``tools``.

    Other non-matching lines (e.g. ``Tool Inputs:``, LLM message lines) are
    skipped. ``trace_id`` is injected from the dict key so downstream code does
    not need to carry it separately.

    Args:
        traces: ``{trace_id: formatted_trace_string}`` as stored in a
                ``TraceReport`` (inline or loaded from an offload file).

    Returns:
        Flat list of slim run dicts, one entry per parsed ``[depth=N]`` line,
        each guaranteed to have a ``trace_id`` key.
        E.g. {'id': '019e48ce-a89e-70f0-bd8f-5afe7b6c7990', 'trace_id': '019e48ce-a89e-70f0-bd8f-5afe7b6c7990', 'name': 'fetch_arxiv', 'status': 'success', 'latency': 8.39813, 'error': None, 'run_type': 'tool', 'total_cost': 0.0, 'total_tokens': 0, 'tags': [], 'metadata': {'revision_id': '9498c25-dirty'}, 'inputs': {'query': 'LLM multimodal'}, 'outputs': {'output': {'authors': ['Chanhui Lee', 'Hanbum Ko', 'Yuheon Song', 'YongJun Jeong', 'Rodrigo Hormazabal', 'Sehui Han', 'Kyunghoon Bae', 'Sungbin Lim', 'Sungwoong Kim'], 'metadata': {'arxiv_id': '2502.02810v2', 'categories': ['cs.LG', 'cs.AI', 'physics.chem-ph', 'q-bio.BM'], 'doi': None, 'published': '2025-02-05', 'updated': '2025-05-26', 'url': 'http://arxiv.org/abs/2502.02810v2'}, 'pdf_path': '/Users/dangphuonganh/Documents/llm_wiki/wiki/raw/papers/mol_llm_multimodal_generalist_molecular_llm_with_improved_graph_utilization.pdf', 'title': 'Mol-LLM: Multimodal Generalist Molecular LLM with Improved Graph Utilization'}}}

    """
    runs: list[dict[str, Any]] = []
    for trace_id, text in traces.items():
        context_name: str | None = None
        for line in text.splitlines():
            stripped = line.strip()
            frame = _TRACE_FRAME_RE.match(stripped)
            # If the content after the depth tag is plain text (not JSON),
            # remember it as the context name for the following runs
            if frame and not frame.group(1).startswith("{"):
                context_name = frame.group(1)

            m = _RUN_LINE_RE.match(stripped)
            if not m:
                continue
            try:
                slim = ast.literal_eval(m.group(1))
                if isinstance(slim, dict):
                    slim["context_name"] = context_name
                    slim["trace_id"] = trace_id
                    
                    runs.append(slim)
            except (ValueError, SyntaxError):
                pass
    return runs


def _flow(run: dict) -> str | None:
    """Return the flow label for known project tool runs, otherwise None.

    Flow labels are only meaningful for first-party tools listed in
    ``TOOL_FLOWS``. Chain/LLM wrapper spans such as ``model``, ``tools``, and
    ``ChatAnthropic`` intentionally have no flow.
    """
    if run.get("run_type") != "tool":
        return None
    return TOOL_FLOWS.get(run.get("name", ""))


def _is_eval_run(run: dict) -> bool:
    """Return True if the run belongs to a LangSmith evaluation experiment and should be skipped."""
    return "ls_experiment_id" in (run.get("metadata") or {})


# ---------------------------------------------------------------------------
# compute_baselines_async  (scheduled maintenance tool)
# ---------------------------------------------------------------------------

@tool
async def compute_baselines_async(report: TraceReport) -> dict:
    """Compute per-name and per-flow baselines from a TraceReport and persist them.

    Intended to run on a schedule (weekly) so baselines reflect a rolling
    window of real traffic. Merges into BASELINES_PATH — only entries with
    >= MINIMUM_SAMPLES this window are updated; absent entries are preserved.

    Baselines computed:
    - by_name: median latency + median tokens per run name (llm runs only for tokens)
    - by_flow: median step count per flow label, where step count = number of
      completed runs (latency not None) sharing the same flow within one trace
    - excluded from baseline stats: run names ``model``, ``tools``,
      ``ChatAnthropic`` (LangGraph aggregate/internal spans), plus LangSmith
      eval runs carrying ``ls_experiment_id`` in metadata
    - hard errors are not evaluated here; ``detect_anomalies_async`` still
      flags hard errors for baseline-excluded names

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

    # Merge into existing file — only overwrite entries that have fresh data
    # (>= MINIMUM_SAMPLES this window). Entries absent from this run are left
    # untouched so a quiet week doesn't wipe out hard-won baselines.
    existing = _load_baselines()
    merged = {
        "by_name": {**existing.get("by_name", {}), **by_name},
        "by_flow": {**existing.get("by_flow", {}), **by_flow},
    }
    BASELINES_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINES_PATH.write_text(json.dumps(merged, indent=2))
    return merged


# ---------------------------------------------------------------------------
# detect_anomalies_async  (called during trace analysis)
# ---------------------------------------------------------------------------

def _load_baselines() -> dict:
    """Load persisted baselines from disk, returning empty dicts if the file does not exist yet."""
    if not BASELINES_PATH.exists():
        return {"by_name": {}, "by_flow": {}}
    return json.loads(BASELINES_PATH.read_text())


def _is_failure(run: dict, baselines: dict, trace_flow_counts: dict) -> tuple[bool, list[AnomalyError], list[str]]:
    """Check a single slim run dict for anomaly signals against pre-loaded baselines.
    
    - by name: median latency + median tokens per run name (llm runs only for tokens)
    - by flow: median step count per flow label, where step count = number of
      completed steps (latency not None) sharing the same flow within one trace

    Args:
        run:               Slim run dict from ``_parse_runs()``.
        baselines:         Loaded from ``_load_baselines()``.
        trace_flow_counts: ``{trace_id: {flow: completed_run_count}}`` for the current report,
                           used to evaluate step-count spikes.

    Returns:
        ``(is_anomalous, errors, signals)`` — errors/signals are empty when
        the run is clean.
    """
    if _is_eval_run(run):
        return False, [], []

    # run_type=="chain", name=="LangGraph" is the top-level LangGraph framework
    # span. Errors here are framework/infra noise (state corruption, OOM) — not
    # user-code bugs. Other named chain nodes are custom graph nodes and can
    # surface real agent logic failures worth tracking.
    if run.get("run_type") == "chain" and run.get("name") == "LangGraph":
        return False, [], []

    errors: list[AnomalyError] = []
    signals: list[str] = []
    name = run.get("name", "unknown")
    # None when this run name has no baseline yet (fewer than MINIMUM_SAMPLES seen)
    by_name = baselines["by_name"].get(name, {})

    # always flag hard errors regardless of baselines
    error = run.get("error")
    if error:
        errors.append("hard_error")
        signals.append(f"hard_error:{str(error).splitlines()[0][:80]}")

    # latency spike: skip if no baseline exists for this run name
    latency = run.get("latency")
    median_latency = by_name.get("median_latency")
    if latency is not None and median_latency is not None:
        if latency > _SPIKE_MULTIPLIER * median_latency:
            errors.append("latency_spike")
            signals.append(f"latency_spike:{latency:.1f}s_vs_median_{median_latency:.1f}s")

    # token blowout: only fires for llm runs; tool runs always report 0 tokens
    tokens = run.get("total_tokens")
    median_tokens = by_name.get("median_tokens")
    if tokens and median_tokens is not None:
        if tokens > _SPIKE_MULTIPLIER * median_tokens:
            errors.append("token_blowout")
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
                errors.append("step_count_spike")
                signals.append(f"step_count_spike:flow-{flow}:{actual}_vs_median_{median_steps:.0f}")

    return bool(errors), errors, signals


@tool
async def detect_anomalies_async(report: TraceReport) -> AnomalyReport:
    """Detect anomalous runs in a TraceReport using persisted baselines.

    Loads baselines from the file written by ``compute_baselines_async``.
    If no baselines file exists yet, only hard_error signals will fire.
    Skips LangSmith eval runs carrying ``ls_experiment_id`` in metadata.
    Hard errors are checked for every other parsed run, including names excluded
    from baseline computation (``model``, ``tools``, ``ChatAnthropic``).

    Args:
        report: From ``run_trace_report_async()``.

    Returns:
        AnomalyReport with total_runs_analyzed, anomalous_run_count, and a
        list of AnomalySignal — one per trace — each containing typed anomaly
        categories, detailed signal strings, and a failed_spans list preserving
        every failed span intact.

        E.g. trace_id='019e3ead-268d-7df3-9b1f-3e5e4d2ac2f8' signals=["hard_error:<HTTPError 429: 'Unknown Error'>Traceback (most recent call last):", "hard_error:<HTTPError 429: 'Unknown Error'>"] failed_spans=[FailedSpan(id='019e3ead-268d-7df3-9b1f-3e5e4d2ac2f8', run_name='fetch_arxiv', run_type='tool', flow='wiki-ingestion', inputs={'input': "{'query': '2004.07606'}"}, outputs={}, signals=["hard_error:<HTTPError 429: 'Unknown Error'>Traceback (most recent call last):"]), FailedSpan(id='019e3ead-2691-7b31-9d40-ba4437b78728', run_name='fetch_arxiv', run_type='tool', flow='wiki-ingestion', inputs={'query': '2004.07606'}, outputs={'output': None}, signals=["hard_error:<HTTPError 429: 'Unknown Error'>"])]
        """
    traces = _load_traces(report) # {trace_id: formatted_string}
    # what format of runs? 
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

    # Collect failed spans, group by trace_id, and emit one AnomalySignal per
    # trace. All failed spans are preserved intact as FailedSpan entries so no
    # data is lost when the same error propagates through wrapper + actual call.
    by_trace: dict[str, list[tuple[dict, list[AnomalyError], list[str]]]] = defaultdict(list)
    for r in runs:
        failed, errors, signals = _is_failure(r, baselines, trace_flow_counts)
        if failed:
            by_trace[r.get("trace_id", "")].append((r, errors, signals))

    anomalies: list[AnomalySignal] = []
    for tid, items in by_trace.items():
        seen_errors: set[AnomalyError] = set()
        merged_errors: list[AnomalyError] = []
        seen_signals: set[str] = set()
        merged_signals: list[str] = []
        failed_spans: list[FailedSpan] = []
        for r, errs, sigs in items:
            failed_spans.append(FailedSpan(
                id=r.get("id", ""),
                run_name=r.get("name", "unknown"),
                run_type=r.get("run_type"),
                flow=_flow(r),
                context_name=r.get("context_name"),
                inputs=r.get("inputs") or {},
                outputs=r.get("outputs") or {},
                errors=errs,
                signals=sigs,
            ))
            for e in errs:
                if e not in seen_errors:
                    seen_errors.add(e)
                    merged_errors.append(e)
            for s in sigs:
                if s not in seen_signals:
                    seen_signals.add(s)
                    merged_signals.append(s)
        anomalies.append(AnomalySignal(
            trace_id=tid,
            errors=merged_errors,
            signals=merged_signals,
            failed_spans=failed_spans,
        ))

    return AnomalyReport(
        total_runs_analyzed=len(runs),
        anomalous_run_count=len(anomalies),
        anomalies=anomalies,
    )
