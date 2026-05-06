"""Tests for fetch_traces processing logic.

Fixtures are raw LangSmith runs saved as JSON (see save_fixtures.py).
Loaded back as SimpleNamespace objects — the processing code only uses
top-level dot-attribute access (run.trace_id, run.run_type, etc.) so no
need to reconstruct the full Pydantic Run model.

AsyncClient.read_run is mocked to return the same SimpleNamespace objects,
so no network calls are made during test runs.

Run: 
    # use uv to get the correct python version
    uv run pytest -v -s tests/test_fetch_traces.py::test_offload_when_large
    
    # run only tests with the expect_exception marker
    uv run pytest -m expect_exception
"""
from __future__ import annotations

import asyncio
import json
import math
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import uuid

import pytest

from src.tools.fetch_traces import (
    _MAX_TRACE_CHARS,
    _TOOL_CONTENT_TRUNCATE_THRESHOLD,
    _VERBOSE_TOOLS,
    _group_formatted_traces_async,
    _make_trace_report,
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
def mock_async_client(runs: list[SimpleNamespace]) -> Any:
    """AsyncClient whose read_run returns the matching run from the fixture.

    _format_trace_async calls client.read_run for each llm run to fetch full
    inputs. The mock returns the same SimpleNamespace from the fixture so no
    network call is needed.
    """
    run_map = {str(r.id): r for r in runs}
    class _FakeClient:
        async def read_run(self, run_id: str) -> SimpleNamespace:
            return run_map.get(run_id, runs[0])

    client = _FakeClient()
    return client


_REPORT_BASE: dict[str, Any] = dict(
    project="test",
    fetched_at="2026-01-01T00:00:00+00:00",
    start_time="2026-01-01T00:00:00+00:00",
    end_time="2026-01-01T01:00:00+00:00",
    error_count=0,
    total_cost=0.0,
)

# ---------------------------------------------------------------------------
# Test 1 — auto-offload: is_offloaded=True when chars exceed threshold
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_offload_when_large(
    tmp_path: Path,
    runs: list[SimpleNamespace],
    mock_async_client: Any,
) -> None:
    traces = await _group_formatted_traces_async(runs, mock_async_client)
    # threshold=0 guarantees any non-empty trace set triggers offload
    report = _make_trace_report(
        **_REPORT_BASE,
        traces=traces,
        run_count=len(runs),
        offload_dir=tmp_path,
        threshold=0,
    )
    assert report.is_offloaded is True
    assert report.traces is None
    assert report.traces_path is not None
    assert Path(report.traces_path).exists(), "offload file must be written to disk"


# ---------------------------------------------------------------------------
# Test 2 — inline: is_offloaded=False when chars are small
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_inline_when_small(
    tmp_path: Path,
    runs: list[SimpleNamespace],
    mock_async_client: Any,
) -> None:
    traces = await _group_formatted_traces_async(runs, mock_async_client)
    total_chars = sum(len(v) for v in traces.values())
    # threshold above actual size guarantees inline path
    report = _make_trace_report(
        **_REPORT_BASE,
        traces=traces,
        run_count=len(runs),
        offload_dir=tmp_path,
        threshold=total_chars + 1,
    )
    assert report.is_offloaded is False
    assert report.traces is not None
    assert report.traces_path is None
    assert set(report.traces.keys()) == set(traces.keys())


# ---------------------------------------------------------------------------
# Test 3 — _group_formatted_traces_async groups runs correctly by trace_id
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_groups_by_trace_id(runs: list[SimpleNamespace], mock_async_client: Any) -> None:
    traces = await _group_formatted_traces_async(runs, mock_async_client)
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
async def test_max_trace_chars_respected(runs: list[SimpleNamespace], mock_async_client: Any) -> None:
    traces = await _group_formatted_traces_async(runs, mock_async_client)
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

# ---------------------------------------------------------------------------
# Test 8 — _summarize_traces_async: Anthropic API call is replaceable with fake
#
# Patches _ASYNC_CLIENT.messages.parse so no real API calls are made.
# The real offset/limit slicing logic still runs — only the LLM call is faked.
# ---------------------------------------------------------------------------

from src.tools.summarize_traces import TraceSummaryList, TraceSummary, _summarize_traces_async


def _fake_trace_summary() -> TraceSummary:
    return TraceSummary(
        trace_id=str(uuid.uuid4()),
        session_summary="fake summary",
        status="success",
        error_type="none",
        latency_s=0.0,
        total_cost=0.0,
        llm_turns=1,
    )


@pytest.mark.asyncio
async def test_summarize_batches_fired_in_parallel(
    tmp_path: Path,
    runs: list[SimpleNamespace],
    mock_async_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With >50 traces, ceil(N/50) parse calls must be fired concurrently.

    Parallelism is verified by injecting a 0.05 s delay into the fake and
    checking total wall-time is ~1 batch delay, not N * batch delay.
    """

    # format traces into a dict of {trace_id: denoised messages}
    traces = await _group_formatted_traces_async(runs, mock_async_client)
    if len(traces) < 51:
        # runs.json may collapse to a small number of unique trace_ids.
        # Clone trace text into synthetic ids so we can test multi-batch behavior.
        seed_items = list(traces.items())
        traces = {
            f"{trace_id}__copy_{i}": text
            for i, (trace_id, text) in enumerate(seed_items * 60)
        }

    # create TraceReport
    report = _make_trace_report(
        **_REPORT_BASE,
        traces=traces,
        run_count=len(runs),
        offload_dir=tmp_path,
        threshold=0,  # force offload so trace_count is set
    )

    # Computes how many batches should exist at limit=50
    batch_size = 50
    num_batches = math.ceil(report.trace_count / batch_size)
    FAKE_DELAY = 0.05

    call_count = 0

    # return fake parse() 
    async def slow_fake_parse(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(FAKE_DELAY)
        fake_response = SimpleNamespace(
            parsed_output=TraceSummaryList(items=[_fake_trace_summary()])
        )
        return fake_response

    class _FakeMessages:
        async def parse(self, *args, **kwargs):
            return await slow_fake_parse(*args, **kwargs)

    class _FakeAnthropicClient:
        messages = _FakeMessages()

    # Replaces Anthropic client (_ASYNC_CLIENT.messages.parse) with a fake async function
    monkeypatch.setattr(
        "src.tools.summarize_traces._ASYNC_CLIENT",
        _FakeAnthropicClient(),
    )

    # One default call should fan out all pages in parallel internally.
    start = time.perf_counter()
    results = await _summarize_traces_async(report)
    elapsed = time.perf_counter() - start

    assert isinstance(results, list)
    assert call_count == num_batches # parse called once per internal page
    assert elapsed < FAKE_DELAY * num_batches, (
        f"Calls look sequential: {elapsed:.3f}s >= {FAKE_DELAY * num_batches:.3f}s"
    ) # total elapsed time is less than sequential time (delay * num_batches) → this is the evidence they ran concurrently