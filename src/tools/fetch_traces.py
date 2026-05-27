"""LangSmith → formatted traces → ``TraceReport``.

Call graph (arrows = calls / uses)::

    run_trace_report_async ──► _run_trace_report_async
    run_trace_report ────────► _run_trace_report_async
    main ────────────────────► run_trace_report

    _run_trace_report_async
        │
        ├─► AsyncClient.list_runs
        │
        ├─► _group_formatted_traces_async
        │       │
        │       └─► _format_trace_async  (one per trace, asyncio.gather)
        │               │
        │               ├─► AsyncClient.read_run  (llm runs only)
        │               ├─► _slim
        │               ├─► _denoise_messages  (llm inputs)
        │               │       ├─► _flatten_messages
        │               │       ├─► _role_from_message
        │               │       └─► _redact_content
        │               └─► _denoise_outputs   (llm outputs)
        │
        └─► _make_trace_report  (decides inline vs offload, returns TraceReport)

Example usage:
```python
from src.tools.fetch_traces import run_trace_report_async
r = await run_trace_report_async.ainvoke({"limit": 10, "days": 5})
```
"""

from __future__ import annotations

import argparse
import copy
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from pydantic import BaseModel, model_validator
from langsmith import AsyncClient
from langchain.tools import tool
import asyncio
from src.tools.utils import TRACE_ANALYSIS_TOOLS

TRACE_OFFLOAD_DIR = Path(__file__).parent / "trace_offloads"
_TOOL_CONTENT_TRUNCATE_THRESHOLD = 700 # threshold overwhich we truncate tool content
_MAX_TRACE_CHARS = 50_000 # max chars of trace text to keep
# Auto-offload when total formatted trace text exceeds this threshold.
_OFFLOAD_THRESHOLD_CHARS = 10_000
_STRIP_KEYS = {"invalid_tool_calls", "response_metadata", "usage_metadata", "id", "tool_call_id"}
_VERBOSE_TOOLS = {
    "read_file",
    "write_file",
    "run_trace_report_async",
    "summarize_traces_async",
    "ls",
    "edit_file",
    "grep",
    "execute",
}

class TraceReport(BaseModel):
    project: str
    fetched_at: str
    start_time: str
    end_time: str
    run_count: int
    trace_count: int
    trace_chars: int
    error_count: int
    total_cost: float
    is_offloaded: bool
    traces: Optional[dict[str, str]] = None       # set when is_offloaded=False
    traces_path: Optional[str] = None             # set when is_offloaded=True

    @model_validator(mode="after")
    def _check_offload_consistency(self) -> "TraceReport":
        if self.is_offloaded:
            if self.traces_path is None:
                raise ValueError("is_offloaded=True but traces_path is not set")
            if self.traces is not None:
                raise ValueError("is_offloaded=True but traces is also set — must be None")
        else:
            if self.traces is None:
                raise ValueError("is_offloaded=False but traces is not set")
            if self.traces_path is not None:
                raise ValueError("is_offloaded=False but traces_path is also set — must be None")
        return self


def _slim(run: Any) -> dict[str, Any]:
    """Extract a compact, fixed-key summary of a LangSmith run for trace printing.

    Keeps only fields useful for flow analysis (name, status, latency, error,
    run_type, cost, tags). Full run data is fetched separately for llm runs.

    Returns a dict with the following keys:
    - name
    - status
    - latency
    - error
    - run_type
    - total_cost
    - total_tokens
    - metadata (only if not None)
    - tags (only if not None)
    """
    slim: dict[str, Any] = {
        "id": str(getattr(run, "id", "") or ""),
        "trace_id": str(getattr(run, "trace_id", "") or ""),
        "name": getattr(run, "name", None),
        "status": getattr(run, "status", None),
        "latency": getattr(run, "latency", None),
        "error": getattr(run, "error", None),
        "run_type": getattr(run, "run_type", None),
        "total_cost": float(getattr(run, "total_cost", None) or 0),
        "total_tokens": getattr(run, "total_tokens", None),
    }
    tags = getattr(run, "tags", None)
    metadata = getattr(run, "metadata", None)
    feedback_stats = getattr(run, "feedback_stats", None)
    if feedback_stats is not None:
        slim["feedback_stats"] = feedback_stats
    if tags is not None:
        slim["tags"] = tags
    if metadata is not None:
        filtered_metadata = {
            k: v for k, v in metadata.items()
            if k in {"thread_id", "revision_id", "ls_provider", "ls_model_name"}
        }
        if filtered_metadata:
            slim["metadata"] = filtered_metadata
    return slim


def _role_from_message(msg: dict[str, Any]) -> str:
    """Infer a normalized role string from a serialized LangChain message dict.

    LangSmith serializes messages in two possible shapes — checks both:
    - ``kwargs.type`` string (newer format)
    - last element of ``id`` list mapped via class name (older format)
    Returns ``"unknown"`` if neither matches.
    """
    kwargs = msg.get("kwargs", {})
    if isinstance(kwargs, dict) and isinstance(kwargs.get("type"), str):
        return kwargs["type"]

    class_name = (msg.get("id") or [None])[-1]
    mapping = {
        "SystemMessage": "system",
        "HumanMessage": "human",
        "AIMessage": "ai",
        "ToolMessage": "tool",
    }
    return mapping.get(class_name, "unknown")


def _flatten_messages(messages_obj: Any) -> list[dict[str, Any]]:
    """Flatten ``inputs["messages"]`` into a list of message dicts.

    LangSmith / LangChain can store the list in more than one shape; we do not
    assume which one appears. This helper accepts:

    - a flat list of message dicts: ``[msg, msg, ...]``
    - or a single outer list wrapping inner lists: ``[[msg, msg, ...]]`` (and
      similarly, any list element that is itself a list is expanded one level)

    Each ``msg`` is whatever the run recorded (typically serialized LC messages
    with ``kwargs``, ``id``, etc.); role is inferred later by ``_role_from_message``.

    Examples (dicts shortened for readability)::

        # Nested one level — outer list holds a single inner batch
        _flatten_messages([[
            {"id": ["lc", "HumanMessage"], "kwargs": {"content": "hi"}},
            {"id": ["lc", "AIMessage"], "kwargs": {"content": "yo"}},
        ]])
        # → same two dicts in a flat list (order preserved)

        # Already flat — returned as-is (same objects, order preserved)
        _flatten_messages([
            {"id": ["lc", "HumanMessage"], "kwargs": {"content": "a"}},
            {"id": ["lc", "ToolMessage"], "kwargs": {"name": "grep", "content": "..."}},
        ])
        # → that two-element list unchanged in structure
    """
    if not isinstance(messages_obj, list):
        return []

    flat: list[dict[str, Any]] = []
    for item in messages_obj:
        if isinstance(item, dict):
            flat.append(item)
        elif isinstance(item, list):
            for sub in item:
                if isinstance(sub, dict):
                    flat.append(sub)
    return flat


def _redact_content(content: Any, tool_name: str = "") -> Any:
    """Redact or truncate a message's content field to limit trace size.

    - ``_VERBOSE_TOOLS``: fully replaced with ``[redacted — N chars]`` since
      their output is long and not diagnostic (file contents, tool outputs, etc.).
    - All others: kept if ≤ ``_TOOL_CONTENT_TRUNCATE_THRESHOLD``; otherwise truncated
      to head[:500] + note + tail[-200:] so start and end are still visible.
    """
    raw = content if isinstance(content, str) else json.dumps(content)
    if tool_name in _VERBOSE_TOOLS:
        return f"[redacted — {len(raw)} chars]"
    if len(raw) <= _TOOL_CONTENT_TRUNCATE_THRESHOLD:
        return content
    return raw[:500] + f"\n...[truncated — {len(raw)} chars total]...\n" + raw[-200:]


def _denoise_outputs(outputs: dict[str, Any]) -> dict[str, Any]:
    """Strip noise from an LLM run's outputs dict before printing.

    Removes:
    - ``generation_info`` on each generation entry
    - ``text`` on each generation entry (duplicate of ``message.kwargs.content[0].text``)
    - ``message.id`` on each generation entry
    - From ``message.kwargs``: ``id``, ``usage_metadata``,
      ``invalid_tool_calls`` (when empty), and from ``response_metadata``:
      ``model_provider`` and ``stop_sequence``.
    """
    outputs = copy.deepcopy(outputs)
    for generation_list in outputs.get("generations", []):
        for entry in generation_list:
            entry.pop("generation_info", None)
            entry.pop("text", None)  # duplicate of message.kwargs.content[0].text
            msg = entry.get("message", {})
            msg.pop("id", None)
            kwargs = msg.get("kwargs", {})
            kwargs.pop("id", None)
            kwargs.pop("usage_metadata", None)
            if not kwargs.get("invalid_tool_calls"):
                kwargs.pop("invalid_tool_calls", None)
            rm = kwargs.get("response_metadata", {})
            if isinstance(rm, dict):
                rm.pop("model_provider", None)
                rm.pop("stop_sequence", None)
    return outputs


def _denoise_messages(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract denoised non-system messages from an LLM run's inputs dict.

    Applied to each llm run before printing. Performs:
    - Skips system messages (identical across all runs, excluded to save space).
    - Strips ``_STRIP_KEYS`` (ids, metadata fields that add noise without signal).
    - Redacts/truncates ``content`` via ``_redact_content`` based on tool name.
    """
    messages = _flatten_messages(inputs.get("messages", []))
    output: list[dict[str, Any]] = []

    for msg in messages:
        role = _role_from_message(msg)
        if role == "system":
            continue
        kwargs = dict(msg.get("kwargs", {}))  # shallow copy before mutating
        for key in _STRIP_KEYS:
            kwargs.pop(key, None)
        tool_name = kwargs.get("name", "")
        if "content" in kwargs:
            kwargs["content"] = _redact_content(kwargs["content"], tool_name)
        output.append({"role": role, "kwargs": kwargs})
    return output


async def _format_trace_async(trace_id: str, trace_runs: list[Any], client: AsyncClient) -> str:
    """Format a single trace into a concise, human-readable string.

    Each run line is tagged with ``[depth=N]`` (no tree indentation — saves space).
    Full inputs and outputs are fetched via ``client.read_run()`` only for LLM and tool runs
    that carry signal — skipping redundant prefix calls:

    - ``error is not None`` → always fetch (captures the failure context)
    - ``error is None`` AND it is the last LLM call → fetch (the final response)
    - ``error is None`` AND NOT the last LLM call → skip (redundant prefix)

    Inputs are denoised via ``_denoise_messages``; outputs via ``_denoise_outputs``.
    All runs are capped at ``_MAX_TRACE_CHARS`` to guard against bloated traces.
    """
    lines: list[str] = [f"\n=== trace {trace_id} ({len(trace_runs)} runs) ==="]

    llm_runs = [run for run in trace_runs if run.run_type == "llm"]
    tools_runs = [run for run in trace_runs if run.run_type == "tool" and run.name not in TRACE_ANALYSIS_TOOLS] # add tool name filter for safety
    last_llm_id = str(llm_runs[-1].id) if llm_runs else None
    runs_to_fetch = [
        run for run in llm_runs
        if run.error is not None or str(run.id) == last_llm_id
    ] + tools_runs
    full_runs = await asyncio.gather(*[client.read_run(str(run.id)) for run in runs_to_fetch])
    full_run_map = {str(run.id): full for run, full in zip(runs_to_fetch, full_runs)}

    for run in trace_runs:
        depth = len(run.dotted_order.split(".")) - 1
        slim_content = _slim(run)
        name = slim_content.get("name")
        # Middleware chain steps: one line (name only). Match case-insensitively.
        if isinstance(name, str) and "middleware" in name.lower():
            lines.append(f"[depth={depth}] {name}")
            continue

        full_run = full_run_map.get(str(run.id))

        # For tool runs, embed inputs/outputs into the slim dict so the single
        # [depth=N] line is self-contained and parseable by anomaly_detection.
        if full_run is not None and run.run_type == "tool":
            if full_run.inputs:
                slim_content["inputs"] = full_run.inputs
            if full_run.outputs:
                slim_content["outputs"] = full_run.outputs

        lines.append(f"[depth={depth}] {slim_content}")

        if full_run is None:
            continue  # redundant prefix call — skipped

        if run.run_type == "llm":
            lines.append("Input Messages (exclude system prompt):")
            messages = _denoise_messages(full_run.inputs or {})
            if not messages:
                lines.append("(none)")
            else:
                for idx, message in enumerate(messages, start=1):
                    lines.append(f"[{idx}] role={message['role']}" + "" + json.dumps(message["kwargs"], ensure_ascii=False, separators=(",", ":")))

            lines.append("Outputs:")
            if full_run.outputs:
                lines.append(json.dumps(_denoise_outputs(full_run.outputs), ensure_ascii=False, separators=(",", ":")))
            else:
                lines.append("(none)")
    
    result = "\n".join(lines)

    # reserves space for the suffix so len(result) <= _MAX_TRACE_CHARS always holds
    if len(result) > _MAX_TRACE_CHARS:
        suffix = f"\n...[trace truncated — {len(result)} chars total]..."
        result = result[: max(0, _MAX_TRACE_CHARS - len(suffix))] + suffix
    return result


async def _group_formatted_traces_async(runs: list[Any], client: AsyncClient) -> dict[str, str]:
    """Group runs by trace_id and return ``{trace_id: formatted_string}``."""
    grouped: dict[str, list[Any]] = defaultdict(list)
    for run in runs:
        grouped[str(run.trace_id)].append(run)

    trace_ids = list(grouped.keys())
    sorted_traces = [sorted(grouped[tid], key=lambda r: r.dotted_order) for tid in trace_ids]
    results = await asyncio.gather(
        *[_format_trace_async(tid, runs, client) for tid, runs in zip(trace_ids, sorted_traces)]
    )
    return dict(zip(trace_ids, results))


def _make_trace_report(
    *,
    traces: dict[str, str],
    project: str,
    fetched_at: str,
    start_time: str,
    end_time: str,
    run_count: int,
    error_count: int,
    total_cost: float,
    offload_dir: Path = TRACE_OFFLOAD_DIR,
    threshold: int = _OFFLOAD_THRESHOLD_CHARS,
) -> TraceReport:
    """Decide inline vs offload and return a ``TraceReport``.

    Extracted from ``_run_trace_report_async`` so it can be unit-tested with
    an arbitrary ``threshold`` and a temp ``offload_dir`` without touching the
    real offload directory.

    Args:
        traces:      ``{trace_id: formatted_string}`` from ``_format_traces_async``.
        project:     LangSmith project name.
        fetched_at:  ISO timestamp of fetch.
        start_time:  ISO timestamp of earliest run.
        end_time:    ISO timestamp of latest run.
        run_count:   Total number of raw runs fetched.
        error_count: Number of runs with errors.
        total_cost:  Sum of run costs.
        offload_dir: Directory to write the JSON file when offloading.
        threshold:   Char count above which traces are offloaded to disk.
    """
    trace_chars = sum(len(v) for v in traces.values())
    base = dict(
        project=project,
        fetched_at=fetched_at,
        start_time=start_time,
        end_time=end_time,
        run_count=run_count,
        trace_count=len(traces),
        trace_chars=trace_chars,
        error_count=error_count,
        total_cost=total_cost,
    )
    if trace_chars > threshold:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        traces_path = offload_dir / f"{project}_{ts}.traces.json"
        traces_path.parent.mkdir(parents=True, exist_ok=True)
        traces_path.write_text(json.dumps(traces, ensure_ascii=False, indent=2))
        return TraceReport(**base, is_offloaded=True, traces_path=str(traces_path))
    return TraceReport(**base, is_offloaded=False, traces=traces)


async def _run_trace_report_async(
    project: str = "paper2wiki",
    days: int = 7,
    limit: int = 100,
    error: Optional[bool] = False,
) -> TraceReport:
    """Fetch recent runs and return a TraceReport.

    Offloading is decided automatically: if total formatted trace text exceeds
    _OFFLOAD_THRESHOLD_CHARS, traces are written to a JSON file and
    ``traces_path`` is set instead of ``traces``.

    Args:
        project: LangSmith project name.
        days:    How many days back to fetch from.
        limit:   Max runs to fetch.
        error:   bool - default False. Whether to fetch only error runs.
    """
    client = AsyncClient()

    now = datetime.now(timezone.utc)
    start_time = now - timedelta(days=days)
    print("start time:", start_time)

    # POST /runs/query rejects limit > 100 at the API level.
    # Rate limits: ≤7 days → 10 req/10s; >7 days → 3 req/10s.
    # For large limits, omit limit from the body and break locally to avoid
    # burning through rate-limited pagination pages. Caller should keep limit
    # ≤ 100 for >7-day windows to stay well within 3 req/10s.
    _API_MAX_LIMIT = 100
    list_runs_kwargs: dict[str, Any] = {
        "project_name": project,
        "start_time": start_time,
    }
    if error:
        list_runs_kwargs["error"] = error
    if limit <= _API_MAX_LIMIT:
        list_runs_kwargs["limit"] = limit
        all_runs = [run async for run in client.list_runs(**list_runs_kwargs)]
    else:
        all_runs = []
        async for run in client.list_runs(**list_runs_kwargs):
            all_runs.append(run)
            if len(all_runs) >= limit:
                break
    runs = [r for r in all_runs if r.name not in TRACE_ANALYSIS_TOOLS]

    end_time = max((r.end_time for r in runs if r.end_time), default=now)
    actual_start = min((r.start_time for r in runs if r.start_time), default=start_time)

    traces = await _group_formatted_traces_async(runs, client)
    error_count = sum(1 for r in runs if r.error)
    total_cost = sum(float(r.total_cost or 0) for r in runs)

    return _make_trace_report(
        traces=traces,
        project=project,
        fetched_at=now.isoformat(),
        start_time=actual_start.isoformat(),
        end_time=end_time.isoformat(),
        run_count=len(runs),
        error_count=error_count,
        total_cost=total_cost,
    )


@tool()
async def run_trace_report_async(
    project: str = "paper2wiki",
    days: int = 7,
    limit: int = 100,
) -> TraceReport:
    """Fetch recent LangSmith traces for Paper2Wiki agent analysis.

    Offloading is automatic: if total trace text exceeds the threshold, traces
    are written to ``src/tools/trace_offloads/{project}_{YYYYmmdd_HHMMSS}.traces.json``
    and ``report.is_offloaded=True``; otherwise traces are returned inline.

    Args:
        project: LangSmith project name to query (default: "paper2wiki").
        days: Lookback window in days (start_time = now - days).
        limit: Maximum number of runs to fetch. The LangSmith API rejects
               limit > 100 per request; values above 100 are handled by
               client-side pagination (each page is a separate API call).
               Note: >7-day windows are rate-limited to 3 req/10s, so keep
               limit ≤ 100 for large windows to avoid rate limit errors.

    Returns:
        TraceReport dict with keys:
        - project, fetched_at, start_time, end_time
        - run_count, trace_count, trace_chars, error_count, total_cost
        - exactly one of:
          - traces: {trace_id: formatted_trace_str}
          - traces_path: path to JSON file containing that dict (auto-offloaded)
    """
    return await _run_trace_report_async(project, days, limit)


# sync wrapper for CLI
def run_trace_report(
    project: str = "paper2wiki",
    days: int = 7,
    limit: int = 100,
) -> TraceReport:
    """Sync wrapper. Do not call from an active async event loop."""
    return asyncio.run(_run_trace_report_async(project, days, limit))


def main() -> None:
    """CLI entry point — thin shim over run_trace_report()."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch recent LangSmith runs and print a "
            "depth-grouped report with non-system llm messages + outputs."
        )
    )
    parser.add_argument("--project", default="paper2wiki")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args()

    report = run_trace_report(project=args.project, days=args.days, limit=args.limit)
    print(f"project={report.project} runs={report.run_count} traces={report.trace_count} "
          f"chars={report.trace_chars} errors={report.error_count} cost=${report.total_cost:.4f}")
    if report.is_offloaded:
        print(f"traces written to: {report.traces_path}")
    else:
        for text in (report.traces or {}).values():
            print(text)


if __name__ == "__main__":
    main()
