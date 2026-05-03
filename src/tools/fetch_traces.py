"""LangSmith → formatted traces → ``TraceReport``.

Call graph (arrows = calls / uses)::

    run_trace_report_async ──► _run_trace_report_async
    run_trace_report ────────► _run_trace_report_async
    main ────────────────────► run_trace_report

    _run_trace_report_async
        │
        ├─► AsyncClient.list_runs
        │
        └─► _build_trace_report_async
                │
                └─► _format_trace_async  (one per trace, asyncio.gather)
                        │
                        ├─► AsyncClient.read_run  (llm runs only)
                        ├─► _slim
                        └─► _denoise_messages  (llm inputs)
                                ├─► _flatten_messages
                                ├─► _role_from_message
                                └─► _redact_content

    _run_trace_report_async ──► TraceReport  (inline traces or JSON offload;
        ``is_offloaded`` + ``trace_chars`` reflect the decision)

Example usage:
```python
from src.tools.fetch_traces import run_trace_report_async
r = await run_trace_report_async.ainvoke({"limit": 10, "days": 5})
```
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing_extensions import Any, Optional
from pydantic import BaseModel, model_validator
from langsmith import AsyncClient
from langchain.tools import tool
import asyncio

TRACE_OFFLOAD_DIR = Path(__file__).parent / "trace_offloads"
_TOOL_CONTENT_TRUNCATE_THRESHOLD = 700 # threshold overwhich we truncate tool content
_MAX_TRACE_CHARS = 50_000 # max chars of trace text to keep
# Auto-offload when total formatted trace text exceeds this threshold.
# The agent only needs TraceReport metadata inline — trace text is consumed
# by summarize_traces_async, not the agent directly. Keep inline payloads
# small (~5K tokens) to avoid crowding the agent's working context.
_OFFLOAD_THRESHOLD_CHARS = 20_000
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
    """
    return {
        "id": str(getattr(run, "id", "")),
        "name": getattr(run, "name", None),
        "status": getattr(run, "status", None),
        "latency": getattr(run, "latency", None),
        "error": getattr(run, "error", None),
        "run_type": getattr(run, "run_type", None),
        "total_cost": float(getattr(run, "total_cost", None) or 0),
        "tags": getattr(run, "tags", None),
    }


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
    """Format a single trace into a depth-indented, human-readable string.

    Full inputs for llm runs are fetched in parallel via ``client.read_run()``
    because ``list_runs()`` does not return the complete inputs payload.
    All runs are capped at ``_MAX_TRACE_CHARS`` to guard against bloated traces.
    """
    lines: list[str] = [f"\n=== trace {trace_id} ({len(trace_runs)} runs) ==="]

    llm_runs = [run for run in trace_runs if run.run_type == "llm"]
    full_runs = await asyncio.gather(*[client.read_run(str(run.id)) for run in llm_runs])
    full_run_map = {str(run.id): full for run, full in zip(llm_runs, full_runs)}

    for run in trace_runs:
        depth = len(run.dotted_order.split(".")) - 1
        indent = "  " * depth
        lines.append(f"{indent}[depth={depth}] {_slim(run)}")

        if run.run_type != "llm":
            continue
        
        full_run = full_run_map[str(run.id)]

        lines.append(f"{indent}  non-system messages (system prompt identical across all runs):")
        messages = _denoise_messages(full_run.inputs or {})
        if not messages:
            lines.append(f"{indent}    (none)")
        else:
            for idx, message in enumerate(messages, start=1):
                lines.append(f"{indent}    [{idx}] role={message['role']}")
                lines.append(json.dumps(message["kwargs"], ensure_ascii=False, separators=(",", ":")))

        lines.append(f"{indent}  outputs:")
        if full_run.outputs:
            lines.append(json.dumps(full_run.outputs, ensure_ascii=False, separators=(",", ":")))
        else:
            lines.append(f"{indent}    (none)")

    result = "\n".join(lines)

    # reserves space for the suffix so len(result) <= _MAX_TRACE_CHARS always holds
    if len(result) > _MAX_TRACE_CHARS:
        suffix = f"\n...[trace truncated — {len(result)} chars total]..."
        result = result[: max(0, _MAX_TRACE_CHARS - len(suffix))] + suffix
    return result


async def _build_trace_report_async(runs: list[Any], client: AsyncClient) -> dict[str, str]:
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


async def _run_trace_report_async(
    project: str = "paper2wiki",
    days: int = 7,
    limit: int = 100,
) -> TraceReport:
    """Fetch recent runs and return a TraceReport.

    Offloading is decided automatically: if total formatted trace text exceeds
    _OFFLOAD_THRESHOLD_CHARS, traces are written to a JSON file and
    ``traces_path`` is set instead of ``traces``.

    Args:
        project: LangSmith project name.
        days:    How many days back to fetch from.
        limit:   Max runs to fetch.
    """
    client = AsyncClient()

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    start_time = now - timedelta(days=days)

    runs = [
        run async for run in client.list_runs(
            project_name=project,
            start_time=start_time,
            limit=limit,
        )
    ]

    end_time = min((r.end_time for r in runs if r.end_time), default=now)
    actual_start = min((r.start_time for r in runs if r.start_time), default=start_time)

    traces = await _build_trace_report_async(runs, client)
    trace_chars = sum(len(v) for v in traces.values())
    error_count = sum(1 for r in runs if r.error)
    total_cost = sum(float(r.total_cost or 0) for r in runs)

    base = dict(
        project=project,
        fetched_at=now.isoformat(),
        start_time=actual_start.isoformat(),
        end_time=end_time.isoformat(),
        run_count=len(runs),
        trace_count=len(traces),
        trace_chars=trace_chars,
        error_count=error_count,
        total_cost=total_cost,
    )

    if trace_chars > _OFFLOAD_THRESHOLD_CHARS:
        traces_path = TRACE_OFFLOAD_DIR / f"{project}_{timestamp}.traces.json"
        traces_path.parent.mkdir(parents=True, exist_ok=True)
        traces_path.write_text(json.dumps(traces, ensure_ascii=False, indent=2))
        return TraceReport(**base, is_offloaded=True, traces_path=str(traces_path))
    return TraceReport(**base, is_offloaded=False, traces=traces)


@tool
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
        limit: Maximum number of runs to fetch from LangSmith.

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
