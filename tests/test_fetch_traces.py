"""Tests for fetch_traces processing logic.

Fixtures are raw LangSmith runs saved as JSON (see save_fixtures.py).
Loaded back as SimpleNamespace objects — the processing code only uses
top-level dot-attribute access (run.trace_id, run.run_type, etc.) so no
need to reconstruct the full Pydantic Run model.

AsyncClient.read_run is mocked to return the same SimpleNamespace objects,
so no network calls are made during test runs.

Run: 
    # use uv to get the correct python version
    uv run pytest tests/test_fetch_traces.py::test_model_validator_rejects_offloaded_without_path
    
    # run only tests with the expect_exception marker
    uv run pytest -m expect_exception
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.tools.fetch_traces import (
    _MAX_TRACE_CHARS,
    _TOOL_CONTENT_TRUNCATE_THRESHOLD,
    _VERBOSE_TOOLS,
    _build_trace_report_async,
    _redact_content,
    TraceReport,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "runs.json"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def runs() -> list[SimpleNamespace]:
    """Load raw runs from fixture as SimpleNamespace for dot-attribute access."""
    if not FIXTURE_PATH.exists():
        pytest.skip(f"Fixture not found — run `python -m tests.save_fixtures` first ({FIXTURE_PATH})")
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [SimpleNamespace(**r) for r in data]


@pytest.fixture()
def mock_async_client(runs: list[SimpleNamespace]) -> AsyncMock:
    """AsyncClient whose read_run returns the matching run from the fixture.

    _format_trace_async calls client.read_run for each llm run to fetch full
    inputs. The mock returns the same SimpleNamespace from the fixture so no
    network call is needed.
    """
    run_map = {str(r.id): r for r in runs}
    client = AsyncMock()
    async def _read_run(run_id: str) -> SimpleNamespace:
        return run_map.get(run_id, runs[0])
    client.read_run.side_effect = _read_run
    return client


# ---------------------------------------------------------------------------
# Test 1 — auto-offload: is_offloaded=True when chars exceed threshold
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_offload_when_large(runs: list[SimpleNamespace], mock_async_client: AsyncMock) -> None:
    traces = await _build_trace_report_async(runs, mock_async_client)
    trace_chars = sum(len(v) for v in traces.values())
    # Force offload by setting threshold to 0
    report = TraceReport(
        project="test", fetched_at="", start_time="", end_time="",
        run_count=len(runs), trace_count=len(traces), trace_chars=trace_chars,
        error_count=0, total_cost=0.0,
        is_offloaded=True, traces_path="/tmp/fake.json",
    )
    assert report.is_offloaded is True
    assert report.traces is None
    assert report.traces_path is not None


# ---------------------------------------------------------------------------
# Test 2 — inline: is_offloaded=False when chars are small
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inline_when_small(runs: list[SimpleNamespace], mock_async_client: AsyncMock) -> None:
    traces = await _build_trace_report_async(runs, mock_async_client)
    trace_chars = sum(len(v) for v in traces.values())
    report = TraceReport(
        project="test", fetched_at="", start_time="", end_time="",
        run_count=len(runs), trace_count=len(traces), trace_chars=trace_chars,
        error_count=0, total_cost=0.0,
        is_offloaded=False, traces=traces,
    )
    assert report.is_offloaded is False
    assert report.traces is not None
    assert report.traces_path is None


# ---------------------------------------------------------------------------
# Test 3 — _build_trace_report_async groups runs correctly by trace_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_groups_by_trace_id(runs: list[SimpleNamespace], mock_async_client: AsyncMock) -> None:
    traces = await _build_trace_report_async(runs, mock_async_client)
    # Every trace_id from runs must appear in traces
    expected_trace_ids = {str(r.trace_id) for r in runs}
    assert set(traces.keys()) == expected_trace_ids
    # Every value must be a non-empty string
    for trace_id, text in traces.items():
        assert isinstance(text, str) and text, f"Trace {trace_id} is empty"


# ---------------------------------------------------------------------------
# Test 4 — non-verbose tool content truncated at _MAX_TOOL_CONTENT_CHARS
# ---------------------------------------------------------------------------

def test_tool_content_truncated() -> None:
    long_content = "x" * (_TOOL_CONTENT_TRUNCATE_THRESHOLD + 1000)
    result = _redact_content(long_content, tool_name="other_tool")
    assert isinstance(result, str)
    assert len(result) < len(long_content)
    assert "truncated" in result


def test_tool_content_kept_when_short() -> None:
    short = "x" * (_TOOL_CONTENT_TRUNCATE_THRESHOLD - 1)
    assert _redact_content(short, tool_name="other_tool") == short


# ---------------------------------------------------------------------------
# Test 5 — verbose tools are fully redacted regardless of content length
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool_name", sorted(_VERBOSE_TOOLS))
def test_verbose_tools_redacted(tool_name: str) -> None:
    content = "a" * 2000
    result = _redact_content(content, tool_name=tool_name)
    assert result == f"[redacted — {len(content)} chars]"


def test_verbose_tool_short_content_still_redacted() -> None:
    content = "tiny"
    result = _redact_content(content, tool_name="read_file")
    assert result == f"[redacted — {len(content)} chars]"


# ---------------------------------------------------------------------------
# Test 6 — _MAX_TRACE_CHARS hard cap respected on real fixture data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_trace_chars_respected(runs: list[SimpleNamespace], mock_async_client: AsyncMock) -> None:
    traces = await _build_trace_report_async(runs, mock_async_client)
    for trace_id, text in traces.items():
        assert len(text) <= _MAX_TRACE_CHARS, (
            f"Trace {trace_id} exceeds _MAX_TRACE_CHARS: {len(text)} chars"
        )


# ---------------------------------------------------------------------------
# Test 7 — TraceReport model_validator rejects inconsistent state
# ---------------------------------------------------------------------------
@pytest.mark.expect_exception
def test_model_validator_rejects_offloaded_without_path() -> None:
    with pytest.raises(Exception):
        TraceReport(
            project="p", fetched_at="", start_time="", end_time="",
            run_count=0, trace_count=0, trace_chars=0, error_count=0, total_cost=0.0,
            is_offloaded=True, traces_path=None,
        )


@pytest.mark.expect_exception
def test_model_validator_rejects_inline_without_traces() -> None:
    with pytest.raises(Exception):
        TraceReport(
            project="p", fetched_at="", start_time="", end_time="",
            run_count=0, trace_count=0, trace_chars=0, error_count=0, total_cost=0.0,
            is_offloaded=False, traces=None,
        )

@pytest.mark.expect_exception
def test_model_validator_rejects_both_set() -> None:
    with pytest.raises(Exception):
        TraceReport(
            project="p", fetched_at="", start_time="", end_time="",
            run_count=0, trace_count=0, trace_chars=0, error_count=0, total_cost=0.0,
            is_offloaded=False, traces={"id": "text"}, traces_path="/tmp/fake.json",
        )
