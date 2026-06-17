"""Summarize a TraceReport into structured per-trace JSON using Claude Haiku."""
from __future__ import annotations

import json
import anthropic
from src.tools.observability_eval_tools.fetch_traces import TraceReport
from pydantic import BaseModel, Field
from typing_extensions import Optional, Literal, Any
from langchain.tools import tool
import asyncio
_ASYNC_CLIENT = anthropic.AsyncAnthropic()
_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 4192
_SYSTEM_PROMPT = (
    "You are a concise technical analyst for a LLM agent system called Paper2Wiki. "
    "You receive formatted LangSmith trace logs and return structured JSON summaries. "
    "Emphasize any issues or anomalies in the traces. "
    "Return only valid JSON — no markdown fences, no preamble, no explanation."
)

class TraceSummary(BaseModel):
    trace_id: str
    session_summary: str
    status: Literal["success", "error", "pending", "cancelled"]
    error_type: str = Field(description="Short error category if status='error', e.g. 'tool_failure', 'context_limit', 'hitl_rejected'; else 'none'")
    affected_skill: Optional[str] = None
    skill_compliance: Optional[str] = Field(default=None, description="'compliant', 'deviated', or 'not_applicable'")
    deviation_note: Optional[str] = Field(default=None, description="Concise description of what the agent did wrong or unexpectedly; null if compliant")
    latency_s: Optional[float] = None
    total_cost: Optional[float] = None
    llm_turns: Optional[int] = None

class TraceSummaryList(BaseModel):
    summaries: list[TraceSummary]


def _load_traces(report: TraceReport) -> dict[str, str]:
    if not report.is_offloaded:
        return report.traces  # type: ignore[return-value]  # validated non-None by model_validator
    with open(report.traces_path, encoding="utf-8") as f:  # type: ignore[arg-type]
        return json.load(f)


def _filter_traces(traces: dict[str, str], focus_query: str | None) -> dict[str, str]:
    if focus_query is None:
        return traces
    words = [w.lower() for w in focus_query.split() if w]
    filtered = {
        tid: text
        for tid, text in traces.items()
        if any(w in text.lower() for w in words)
    }
    return filtered if filtered else traces


def _build_messages(traces: dict[str, str], focus_query: str | None) -> str:
    """Build the system prompt for the trace summarization.
    We use built in structured output to ensure the output is always valid JSON 
    and will automatically inject the TraceSummary model into prompt"""
    focus_line = f"Focus: {focus_query}" if focus_query else "General analysis."
    parts = [
        f"Analyzing {len(traces)} traces from Paper2Wiki agent.",
        focus_line,
        "",
    ]
    for trace_id, trace_text in traces.items():
        parts.append(f"=== TRACE {trace_id} ===")
        parts.append(trace_text)
        parts.append("")
    return "\n".join(parts)

async def _summarize_batch_async(
    all_traces: list[tuple[str, str]],
    offset: int,
    limit: int,
    focus_query: str | None = None,
) -> list[dict[str, Any]]:
    """Summarize one trace page."""
    sliced = all_traces[offset: offset + limit]
    traces = dict(sliced)
    traces = _filter_traces(traces, focus_query)
    prompt = _build_messages(traces, focus_query)

    response = await _ASYNC_CLIENT.messages.parse(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        output_format=TraceSummaryList,
    )
    return [item.model_dump() for item in response.parsed_output.summaries]

async def _summarize_traces_async(
    report: TraceReport,
    offset: int = 0,
    limit: int = 50,
    focus_query: str | None = None,
) -> list[dict[str, Any]]:
    """Undecorated implementation called by the tool wrapper.

    Default mode (offset=0, limit=50): summarize the full report in parallel
    batches of 50.

    Targeted mode (custom offset/limit): summarize only one selected page.
    """
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if limit > 50:
        raise ValueError("limit must be <= 50 to avoid context overflow")
    if offset < 0:
        raise ValueError("offset must be >= 0")

    all_traces = list(_load_traces(report).items())
    if not all_traces:
        return []

    # Explicit page request (used for retrying one failed batch).
    if offset != 0 or limit != 50:
        return await _summarize_batch_async(all_traces, offset, limit, focus_query)

    # Default tool path: summarize the whole report in parallel pages of 50.
    tasks = [
        _summarize_batch_async(all_traces, batch_offset, 50, focus_query)
        for batch_offset in range(0, len(all_traces), 50)
    ]
    batch_results = await asyncio.gather(*tasks)
    return [item for batch in batch_results for item in batch]


@tool()
async def summarize_traces_async(
    report: TraceReport,
    offset: int = 0,
    limit: int = 50,
    focus_query: str | None = None,
) -> list[dict[str, Any]]:
    """Haiku structured summaries for traces in a ``TraceReport``.

    Default behavior (``offset=0`` and ``limit=50``): summarize the entire
    report automatically by splitting into pages of 50 and running those pages
    in parallel.

    Targeted behavior (custom ``offset``/``limit``): summarize a single page,
    useful for retrying only a failed batch.

    Args:
        report: From ``run_trace_report_async()`` — either ``traces`` (inline)
            or ``traces_path`` (offloaded JSON).
        offset: Index of the first trace to include (0-based). Keep default 0
            to process all pages automatically.
        limit: Traces per page. Default 50. Must be <= 50.
        focus_query: Optional keywords (split on whitespace). Within the
            selected page(s), keeps only traces whose text matches any keyword
            (case-insensitive); if none match, that page is summarized as-is.

    Returns:
        One dict per trace (``TraceSummary`` shape: ``trace_id``,
        ``session_summary``, ``status``, ``error_type``, optional metadata fields).
    """
    return await _summarize_traces_async(report, offset, limit, focus_query)