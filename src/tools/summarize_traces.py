"""Summarize a TraceReport into structured per-trace JSON using Claude Haiku."""
from __future__ import annotations

import json
from typing import Any
import anthropic
from src.tools.trace_report import TraceReport
from pydantic import BaseModel
from typing import Optional
from langchain.tools import tool
import asyncio

_ASYNC_CLIENT = anthropic.AsyncAnthropic()
_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1500
_SYSTEM_PROMPT = (
    "You are a concise technical analyst for a LLM agent system called Paper2Wiki. "
    "You receive formatted LangSmith trace logs and return structured JSON summaries. "
    "Return only valid JSON — no markdown fences, no preamble, no explanation."
)

class TraceSummary(BaseModel):
    trace_id: str
    session_summary: str
    status: str
    error_type: str
    recoverable: Optional[bool] = None
    affected_skill: Optional[str] = None
    skill_compliance: Optional[str] = None
    deviation_note: Optional[str] = None
    latency_s: Optional[float] = None
    total_cost: Optional[float] = None
    llm_turns: Optional[int] = None

class TraceSummaryList(BaseModel):
    items: list[TraceSummary]


def _load_traces(report: TraceReport) -> dict[str, str]:
    traces = report.get("traces")
    if traces is not None:
        return traces
    traces_path = report.get("traces_path")
    if traces_path is not None:
        with open(traces_path, encoding="utf-8") as f:
            return json.load(f)
    raise ValueError("TraceReport has neither 'traces' nor 'traces_path'")


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

async def _summarize_traces_async(
    report: TraceReport,
    focus_query: str | None = None,
) -> list[dict[str, Any]]:
    """Summarize traces using the async Anthropic client.

    This is the undecorated implementation. Tool wrappers call this function so
    the sync wrapper does not call a decorated LangChain tool object.
    """
    traces = _load_traces(report)
    traces = _filter_traces(traces, focus_query)
    prompt = _build_messages(traces, focus_query)

    # The parse() method automatically transforms your Pydantic model, validates the response, and returns a parsed_output attribute
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
    focus_query: str | None = None,
) -> list[dict[str, Any]]:
    """Async tool: summarize all traces in a TraceReport.

    Prefer this inside async agent execution. Accepts the TraceReport returned
    by run_trace_report_async() and returns one structured summary per trace.
    """
    return await _summarize_traces_async(report, focus_query)


@tool
def summarize_traces(
    report: TraceReport,
    focus_query: str | None = None,
) -> list[dict[str, Any]]:
    """Sync tool: summarize all traces in a TraceReport.

    Convenience wrapper for synchronous callers. Do not call from an active
    async event loop; use summarize_traces_async() there.
    """
    return asyncio.run(_summarize_traces_async(report, focus_query))