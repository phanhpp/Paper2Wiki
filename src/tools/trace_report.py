"""Print a minimal LangSmith trace report for recent runs.

This script fetches recent runs from a LangSmith project, groups them by trace,
prints each run with hierarchy depth, and for llm runs prints:
- non-system input messages only
- outputs payload

Intended primarily as an agent tool — call ``run_report()`` directly.
The CLI (``__main__``) is a convenience wrapper for manual use.
"""

from __future__ import annotations

import argparse
import json
import pickle
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from langsmith import Client

CACHE_DIR = Path(__file__).parent / "trace_cache"
FETCH_LOG = CACHE_DIR / "fetch_log.jsonl"


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


def content_to_text(content: Any) -> str:
    """Convert LangChain-style content payload (str or list blocks) into plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if isinstance(block.get("text"), str):
                    parts.append(block["text"])
                elif isinstance(block.get("content"), str):
                    parts.append(block["content"])
        return "".join(parts)
    return str(content)


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
    word_count = len(raw.split())
    return f"[redacted — {char_count} chars, {word_count} words]"


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


def print_trace_report(runs: list[Any], client: Client) -> None:
    """Group runs by trace and print run tree with llm-only message/output details.

    For llm runs, full inputs are fetched via client.read_run() because
    list_runs() does not return complete inputs payload.
    """
    traces: dict[str, list[Any]] = defaultdict(list)
    for run in runs:
        trace_id = str(run.trace_id)
        traces[trace_id].append(run)

    for trace_id, trace_runs in traces.items():
        trace_runs.sort(key=lambda r: r.dotted_order)
        print(f"\n=== trace {trace_id} ({len(trace_runs)} runs) ===")

        for run in trace_runs:
            depth = len(run.dotted_order.split(".")) - 1
            indent = "  " * depth
            print(f"{indent}[depth={depth}] {slim(run)}")

            if run.run_type != "llm":
                continue

            full_run = client.read_run(str(run.id))

            print(f"{indent}  non-system messages (system prompt identical across all runs):")
            messages = non_system_messages(full_run.inputs or {})
            if not messages:
                print(f"{indent}    (none)")
            else:
                for idx, message in enumerate(messages, start=1):
                    print(f"{indent}    [{idx}] role={message['role']}")
                    print(json.dumps(message["kwargs"], ensure_ascii=False, indent=2))

            print(f"{indent}  outputs:")
            if full_run.outputs:
                print(json.dumps(full_run.outputs, ensure_ascii=False, indent=2))
            else:
                print(f"{indent}    (none)")


def _log_fetch(meta: dict[str, Any]) -> None:
    """Append a fetch record to the JSONL fetch log."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with FETCH_LOG.open("a") as f:
        f.write(json.dumps(meta) + "\n")


def save_runs(runs: list[Any], cache_path: Path) -> None:
    """Pickle runs to disk for offline testing."""
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


def run_trace_report(
    project: str = "paper2wiki",
    days: int = 4,
    limit: int = 70,
    cache_path: Path | None = None,
) -> None:
    """Fetch recent runs and print the trace report.

    This is the main entry point for agent/code use — no argparse.

    Args:
        project:    LangSmith project name.
        days:       How many days back to fetch from.
        limit:      Max runs to fetch.
        cache_path: If given, pickle fetched runs to this path for offline replay.
                    Defaults to CACHE_DIR/<project>_<timestamp>.pkl.
    """
    client = Client()

    now = datetime.now(timezone.utc)
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

    if cache_path is None:
        cache_path = CACHE_DIR / f"{project}_{now.strftime('%Y%m%d_%H%M%S')}.pkl"
    save_runs(runs, cache_path)

    _log_fetch({
        "project": project,
        "fetched_at": now.isoformat(),
        "start_time": actual_start.isoformat(),
        "end_time": end_time.isoformat(),
        "limit": limit,
        "run_count": len(runs),
        "cache_path": str(cache_path),
    })

    print(f"Fetched {len(runs)} runs (limit={limit}) for project={project}")
    print_trace_report(runs, client)


def run_from_cache(cache_path: Path | str) -> None:
    """Load runs from a pickle cache and print the trace report without fetching.

    Use this during development to re-run print_trace_report on the same data.
    """
    client = Client()
    runs = load_runs(Path(cache_path))
    print_trace_report(runs, client)


def main() -> None:
    """CLI entry point — thin shim over run_trace_report() / run_from_cache()."""
    parser = argparse.ArgumentParser(
        description=(
            "Fetch recent LangSmith runs and print a "
            "depth-grouped report with non-system llm messages + outputs."
        )
    )
    parser.add_argument("--project", default="paper2wiki")
    parser.add_argument("--days", type=int, default=4)
    parser.add_argument("--limit", type=int, default=70)
    parser.add_argument("--from-cache", metavar="PATH", help="Load runs from pickle cache instead of fetching")
    args = parser.parse_args()

    if args.from_cache:
        run_from_cache(args.from_cache)
    else:
        run_trace_report(project=args.project, days=args.days, limit=args.limit)


if __name__ == "__main__":
    main()
