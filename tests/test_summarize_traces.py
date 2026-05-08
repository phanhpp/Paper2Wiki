"""Unit tests for trace summarizer helpers (filter, load, validation) without Anthropic calls."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tools.fetch_traces import TraceReport
from src.tools.summarize_traces import _filter_traces, _load_traces, _summarize_traces_async


@pytest.mark.unit
def test_filter_traces_returns_subset_when_query_matches() -> None:
    """``focus_query`` keeps only traces whose text contains a query word (case-insensitive)."""
    traces = {
        "a": "tool failure on fetch_arxiv",
        "b": "clean successful run",
    }
    filtered = _filter_traces(traces, "failure")
    assert filtered == {"a": "tool failure on fetch_arxiv"}


@pytest.mark.unit
def test_filter_traces_falls_back_to_all_when_no_match() -> None:
    """If no trace matches any keyword, the full dict is summarized (no empty filter)."""
    traces = {"a": "first", "b": "second"}
    filtered = _filter_traces(traces, "notfound")
    assert filtered == traces


@pytest.mark.unit
def test_load_traces_reads_offloaded_json(tmp_path: Path) -> None:
    """Offloaded ``TraceReport`` loads ``{trace_id: text}`` from ``traces_path`` JSON."""
    traces_path = tmp_path / "traces.json"
    payload = {"tid-1": "trace text"}
    traces_path.write_text(json.dumps(payload), encoding="utf-8")

    report = TraceReport(
        project="p",
        fetched_at="",
        start_time="",
        end_time="",
        run_count=1,
        trace_count=1,
        trace_chars=10,
        error_count=0,
        total_cost=0.0,
        is_offloaded=True,
        traces_path=str(traces_path),
    )
    assert _load_traces(report) == payload


@pytest.mark.unit
@pytest.mark.asyncio
async def test_summarize_rejects_invalid_limits() -> None:
    """``limit`` must be <= 50 and ``offset`` must be non-negative before any LLM work."""
    report = TraceReport(
        project="p",
        fetched_at="",
        start_time="",
        end_time="",
        run_count=1,
        trace_count=1,
        trace_chars=5,
        error_count=0,
        total_cost=0.0,
        is_offloaded=False,
        traces={"t1": "hello"},
    )

    with pytest.raises(ValueError, match="limit must be <= 50"):
        await _summarize_traces_async(report, limit=51)

    with pytest.raises(ValueError, match="offset must be >= 0"):
        await _summarize_traces_async(report, offset=-1)
