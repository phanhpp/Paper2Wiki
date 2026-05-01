"""Print a minimal LangSmith trace report for recent runs.

This script fetches recent runs from a LangSmith project, groups them by trace,
prints each run with hierarchy depth, and for llm runs prints:
- non-system input messages only
- outputs payload

Intended primarily as an agent tool — call ``run_report()`` directly.
The CLI (``__main__``) is a convenience wrapper for manual use.

Fetch logging:
- Each call appends one JSONL record to ``trace_cache/fetch_log.jsonl`` with
  project, fetched_at, start_time, end_time, limit, run_count, and offload.
- The log stores metadata only; it does not cache LangSmith Run objects.
- By default, formatted traces are returned inline in ``TraceReport["traces"]``.
- With ``offload=True``, formatted traces are written to a JSON file and the
  returned report contains ``TraceReport["traces_path"]`` instead.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, NotRequired, TypedDict
from langsmith import Client

CACHE_DIR = Path(__file__).parent / "trace_cache"
FETCH_LOG = CACHE_DIR / "fetch_log.jsonl"

class TraceReport(TypedDict):
    project: str
    fetched_at: str
    start_time: str
    end_time: str
    run_count: int
    trace_count: int
    error_count: int
    total_cost: float
    # Exactly one of traces or traces_path is present — never both, never neither.
    # offload=False (default): traces is set, traces_path is absent.
    # offload=True: traces_path is set, traces is absent (content written to JSON file).
    traces: NotRequired[dict[str, str]]  # trace_id → formatted string
    traces_path: NotRequired[str]        # path to JSON file containing traces dict


def slim(run: Any) -> dict[str, Any]:
    """Return a compact run summary for quick trace printing."""
    return {
        "id": str(run.id),
        "name": run.name,
        "status": run.status,
        "latency": run.latency,
        "error": run.error,
        "run_type": run.run_type,
        "total_cost": float(run.total_cost or 0),
        "tags": run.tags,
    }


def role_from_message(msg: dict[str, Any]) -> str:
    """Infer a normalized role string from a serialized LangChain message dict."""
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


def flatten_messages(messages_obj: Any) -> list[dict[str, Any]]:
    """Normalize messages into a flat list of message dicts.

    LangSmith run inputs may store messages as either:
    - [msg1, msg2, ...]
    - [[msg1, msg2, ...]] (batched/nested)
    This helper supports both shapes so downstream filtering logic is stable.
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


_VERBOSE_TOOLS = {"read_file", "write_file"}


def _maybe_redact(content: Any, tool_name: str) -> Any:
    """Apply smart redaction to tool content.

    - If tool is verbose (e.g. read_file) AND content contains "error" (case-sensitive):
      keep full content, prepend [ERROR].
    - If tool is verbose and no error: replace with char/word count summary.
    - Otherwise: return content unchanged.
    """
    if tool_name not in _VERBOSE_TOOLS:
        return content

    raw = content if isinstance(content, str) else json.dumps(content)
    if "error" in raw.lower():
        return f"[ERROR] {content}"

    char_count = len(raw)
    if char_count <= 700:
        return content
    return raw[:500] + f"\n...[truncated — {char_count} chars total]...\n" + raw[-200:]


def non_system_messages(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """Return non-system messages as (role, kwargs) pairs.

    kwargs is returned as-is (structure preserved) with only the content field
    potentially modified by _maybe_redact(). No field extraction is done.
    """
    messages = flatten_messages(inputs.get("messages", []))
    output: list[dict[str, Any]] = []

    for msg in messages:
        role = role_from_message(msg)
        if role == "system":
            continue
        kwargs = dict(msg.get("kwargs", {}))  # shallow copy before mutating
        tool_name = kwargs.get("name", "")
        if "content" in kwargs:
            kwargs["content"] = _maybe_redact(kwargs["content"], tool_name)
        output.append({"role": role, "kwargs": kwargs})
    return output


def _format_trace(trace_id: str, trace_runs: list[Any], client: Client) -> str:
    """Format a single trace's runs into a human-readable string."""
    lines: list[str] = [f"\n=== trace {trace_id} ({len(trace_runs)} runs) ==="]

    for run in trace_runs:
        depth = len(run.dotted_order.split(".")) - 1
        indent = "  " * depth
        lines.append(f"{indent}[depth={depth}] {slim(run)}")

        if run.run_type != "llm":
            continue

        full_run = client.read_run(str(run.id))

        lines.append(f"{indent}  non-system messages (system prompt identical across all runs):")
        messages = non_system_messages(full_run.inputs or {})
        if not messages:
            lines.append(f"{indent}    (none)")
        else:
            for idx, message in enumerate(messages, start=1):
                lines.append(f"{indent}    [{idx}] role={message['role']}")
                lines.append(json.dumps(message["kwargs"], ensure_ascii=False, indent=2))

        lines.append(f"{indent}  outputs:")
        if full_run.outputs:
            lines.append(json.dumps(full_run.outputs, ensure_ascii=False, indent=2))
        else:
            lines.append(f"{indent}    (none)")

    return "\n".join(lines)


def build_trace_report(runs: list[Any], client: Client) -> dict[str, str]:
    """Group runs by trace and return {trace_id: formatted_string}.

    For llm runs, full inputs are fetched via client.read_run() because
    list_runs() does not return complete inputs payload.
    """
    grouped: dict[str, list[Any]] = defaultdict(list)
    for run in runs:
        grouped[str(run.trace_id)].append(run)

    return {
        trace_id: _format_trace(trace_id, sorted(trace_runs, key=lambda r: r.dotted_order), client)
        for trace_id, trace_runs in grouped.items()
    }


def _log_fetch(meta: dict[str, Any]) -> None:
    """Append a fetch record to the JSONL fetch log."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with FETCH_LOG.open("a") as f:
        f.write(json.dumps(meta) + "\n")


def run_trace_report(
    project: str = "paper2wiki",
    days: int = 4,
    limit: int = 70,
    offload: bool = False,
) -> TraceReport:
    """Fetch recent runs and return a TraceReport.

    This is the main entry point for agent/code use — no argparse.

    Args:
        project:    LangSmith project name.
        days:       How many days back to fetch from.
        limit:      Max runs to fetch.
        offload:    If True, write traces to a JSON file instead of returning inline.
                    Useful when the report is too large to pass around in memory.
    """
    client = Client()

    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    start_time = now - timedelta(days=days)

    runs = list(
        client.list_runs(
            project_name=project,
            start_time=start_time,
            limit=limit,
        )
    )
    runs = [
        run
        for run in runs
        if ("KeyboardInterrupt" not in (run.error or ""))
        and ("GeneratorExit" not in (run.error or ""))
    ]

    end_time = min((r.end_time for r in runs if r.end_time), default=now)
    actual_start = min((r.start_time for r in runs if r.start_time), default=start_time)

    _log_fetch({
        "project": project,
        "fetched_at": now.isoformat(),
        "start_time": actual_start.isoformat(),
        "end_time": end_time.isoformat(),
        "limit": limit,
        "run_count": len(runs),
        "offload": offload,
    })

    traces = build_trace_report(runs, client)
    error_count = sum(1 for r in runs if r.error)
    total_cost = sum(float(r.total_cost or 0) for r in runs)

    report: TraceReport = {
        "project": project,
        "fetched_at": now.isoformat(),
        "start_time": actual_start.isoformat(),
        "end_time": end_time.isoformat(),
        "run_count": len(runs),
        "trace_count": len(traces),
        "error_count": error_count,
        "total_cost": total_cost,
    }

    if offload:
        traces_path = CACHE_DIR / f"{project}_{timestamp}.traces.json"
        traces_path.parent.mkdir(parents=True, exist_ok=True)
        traces_path.write_text(json.dumps(traces, ensure_ascii=False, indent=2))
        report["traces_path"] = str(traces_path)
    else:
        report["traces"] = traces

    return report


def main() -> None:
    """CLI entry point — thin shim over run_trace_report()."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch recent LangSmith runs and print a "
            "depth-grouped report with non-system llm messages + outputs."
        )
    )
    parser.add_argument("--project", default="paper2wiki")
    parser.add_argument("--days", type=int, default=4)
    parser.add_argument("--limit", type=int, default=70)
    parser.add_argument("--offload", action="store_true", help="Write traces to JSON instead of printing inline")
    args = parser.parse_args()

    report = run_trace_report(
        project=args.project, days=args.days, limit=args.limit, offload=args.offload
    )
    print(f"project={report['project']} runs={report['run_count']} traces={report['trace_count']} "
          f"errors={report['error_count']} cost=${report['total_cost']:.4f}")
    if "traces_path" in report:
        print(f"traces written to: {report['traces_path']}")
    else:
        for text in report.get("traces", {}).values():
            print(text)


if __name__ == "__main__":
    main()
