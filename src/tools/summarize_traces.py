"""Summarize a TraceReport into structured per-trace JSON using Claude Haiku."""
from __future__ import annotations

import json
import anthropic
from src.tools.fetch_traces import TraceReport
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
    items: list[TraceSummary]


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

from pathlib import Path

async def _summarize_traces_async(
    report: TraceReport,
    offset: int = 0,
    limit: int = 50,
    focus_query: str | None = None,
) -> list[dict[str, Any]]:
    """Undecorated implementation called by the tool wrapper."""
    all_traces = list(_load_traces(report).items())
    sliced = all_traces[offset: offset + limit]
    traces = dict(sliced)
    traces = _filter_traces(traces, focus_query)
    prompt = _build_messages(traces, focus_query)

    # DEBUG: dump full prompt to file for inspection
    debug_path = Path(__file__).parent / "trace_offloads" / f"debug_prompt_{offset}.txt"
    debug_path.write_text(prompt, encoding="utf-8")

    # parse() validates response against TraceSummaryList and populates parsed_output
    response = await _ASYNC_CLIENT.messages.parse(
        model=_MODEL,
        max_tokens=_MAX_TOKENS,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
        output_format=TraceSummaryList,
    )
    return [item.model_dump() for item in response.parsed_output.items]


@tool
async def summarize_traces_async(
    report: TraceReport,
    offset: int = 0,
    limit: int = 50,
    focus_query: str | None = None,
) -> list[dict[str, Any]]:
    """Haiku structured summaries for a page of traces in a ``TraceReport``.

    Use ``report["trace_count"]`` to compute how many batches are needed
    (``ceil(trace_count / 50)``), then fire all batches in parallel with
    ``offset=0, 50, 100, …``. Do NOT raise ``limit`` above 50 — doing so
    risks exceeding Haiku's 200K context limit and crashing the call.

    Args:
        report: From ``run_trace_report_async()`` — either ``traces`` (inline)
            or ``traces_path`` (offloaded JSON).
        offset: Index of the first trace to include (0-based). Default 0.
        limit: Number of traces per batch. Default 50; do not exceed 50.
        focus_query: Optional keywords (split on whitespace). Within the
            selected page, keeps only traces whose text matches any keyword
            (case-insensitive); if none match, the full page is used.

    Returns:
        One dict per trace (``TraceSummary`` shape: ``trace_id``,
        ``session_summary``, ``status``, ``error_type``, optional metadata fields).
    """
    return await _summarize_traces_async(report, offset, limit, focus_query)