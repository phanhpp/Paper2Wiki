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
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any
import uuid

import pytest

from src.tools.observability_eval_tools.fetch_traces import (
    _MAX_TRACE_CHARS,
    _TOOL_CONTENT_TRUNCATE_THRESHOLD,
    _VERBOSE_TOOLS,
    _group_formatted_traces_async,
    _format_trace_async,
    _make_trace_report,
    _run_trace_report_async,
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


def _fake_run(
    *,
    run_id: str,
    trace_id: str,
    dotted_order: str,
    run_type: str = "llm",
    error: str | None = None,
    total_cost: float = 0.0,
    name: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> SimpleNamespace:
    """Small LangSmith run stand-in with the attrs used by fetch_traces."""
    return SimpleNamespace(
        id=run_id,
        trace_id=trace_id,
        dotted_order=dotted_order,
        run_type=run_type,
        error=error,
        status="error" if error else "success",
        latency=0.1,
        total_cost=total_cost,
        name=name or f"{run_type}-{run_id}",
        tags=[],
        inputs={
            "messages": [
                {
                    "id": ["langchain", "schema", "messages", "HumanMessage"],
                    "kwargs": {"content": f"input for {run_id}"},
                }
            ]
        },
        outputs={"generations": [[{"message": {"kwargs": {"content": f"output {run_id}"}}}]]},
        start_time=start_time,
        end_time=end_time,
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
    """When total trace chars exceed ``threshold``, report is offloaded to disk and ``traces`` is None."""
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
    """When under the char threshold, ``TraceReport`` keeps ``traces`` inline and no file path."""
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
    """Formatted output has one key per ``trace_id`` and each value is non-empty text."""
    traces = await _group_formatted_traces_async(runs, mock_async_client)
    # Every trace_id from runs must appear in traces
    expected_trace_ids = {str(r.trace_id) for r in runs}
    assert set(traces.keys()) == expected_trace_ids
    # Every value must be a non-empty string
    for trace_id, text in traces.items():
        assert isinstance(text, str) and text, f"Trace {trace_id} is empty"


@pytest.mark.asyncio
async def test_format_trace_fetches_only_error_and_last_llm_runs() -> None:
    """Verify trace formatting fetches only high-signal payloads.

    The formatter fetches:
    - LLM runs that errored (for debugging context)
    - The final LLM run (for agent outcome)
    - All tool runs not in TRACE_ANALYSIS_TOOLS (inputs/outputs embedded inline)

    Successful prefix LLM calls are skipped to keep traces compact.
    """
    trace_runs = [
        _fake_run(run_id="llm-prefix", trace_id="trace-1", dotted_order="20260101.1"),
        _fake_run(
            run_id="tool",
            trace_id="trace-1",
            dotted_order="20260101.2",
            run_type="tool",
        ),
        _fake_run(
            run_id="llm-error",
            trace_id="trace-1",
            dotted_order="20260101.3",
            error="boom",
        ),
        _fake_run(run_id="llm-final", trace_id="trace-1", dotted_order="20260101.4"),
    ]

    class _RecordingClient:
        def __init__(self) -> None:
            self.read_ids: list[str] = []

        async def read_run(self, run_id: str) -> SimpleNamespace:
            self.read_ids.append(run_id)
            return next(run for run in trace_runs if run.id == run_id)

    client = _RecordingClient()
    text = await _format_trace_async("trace-1", trace_runs, client)  # type: ignore[arg-type]

    assert client.read_ids == ["llm-error", "llm-final", "tool"]
    assert "input for llm-prefix" not in text
    assert "input for llm-error" in text
    assert "input for llm-final" in text


@pytest.mark.asyncio
async def test_run_trace_report_async_uses_langsmith_filters_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the public trace-report path wires LangSmith filters into metadata.

    This protects the integration boundary: project/limit/error must be passed
    to ``AsyncClient.list_runs``, and the resulting ``TraceReport`` must reflect
    run counts, error counts, total cost, and observed run time bounds.
    """
    started = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    ended = datetime(2026, 1, 1, 12, 5, tzinfo=timezone.utc)
    runs = [
        _fake_run(
            run_id="root",
            trace_id="trace-a",
            dotted_order="20260101.1",
            run_type="chain",
            total_cost=0.25,
            start_time=started,
            end_time=ended,
        ),
        _fake_run(
            run_id="llm",
            trace_id="trace-a",
            dotted_order="20260101.2",
            total_cost=0.75,
            error="tool failed",
            start_time=started,
            end_time=ended,
        ),
    ]
    captured_kwargs: dict[str, Any] = {}

    class _FakeAsyncClient:
        async def list_runs(self, **kwargs: Any):
            captured_kwargs.update(kwargs)
            for run in runs:
                yield run

        async def read_run(self, run_id: str) -> SimpleNamespace:
            return next(run for run in runs if run.id == run_id)

    monkeypatch.setattr("src.tools.fetch_traces.AsyncClient", _FakeAsyncClient)

    report = await _run_trace_report_async(
        project="paper2wiki-test",
        days=3,
        limit=2,
        error=True,
    )

    assert captured_kwargs["project_name"] == "paper2wiki-test"
    assert captured_kwargs["limit"] == 2
    assert captured_kwargs["error"] is True
    assert report.run_count == 2
    assert report.trace_count == 1
    assert report.error_count == 1
    assert report.total_cost == 1.0
    assert report.start_time == started.isoformat()
    assert report.end_time == ended.isoformat()


# ---------------------------------------------------------------------------
# Test 4 — non-verbose tool content truncated at _MAX_TOOL_CONTENT_CHARS
# ---------------------------------------------------------------------------

def test_tool_content_truncated() -> None:
    """Non-verbose tool output beyond the threshold is shortened with a truncation marker."""
    long_content = "x" * (_TOOL_CONTENT_TRUNCATE_THRESHOLD + 1000)
    result = _redact_content(long_content, tool_name="other_tool")
    assert isinstance(result, str)
    assert len(result) < len(long_content)
    assert "truncated" in result


def test_tool_content_kept_when_short() -> None:
    """Short non-verbose tool content passes through ``_redact_content`` unchanged."""
    short = "x" * (_TOOL_CONTENT_TRUNCATE_THRESHOLD - 1)
    assert _redact_content(short, tool_name="other_tool") == short


# ---------------------------------------------------------------------------
# Test 5 — verbose tools are fully redacted regardless of content length
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool_name", sorted(_VERBOSE_TOOLS))
def test_verbose_tools_redacted(tool_name: str) -> None:
    """Listed verbose tools always get full redaction regardless of payload size."""
    content = "a" * 2000
    result = _redact_content(content, tool_name=tool_name)
    assert result == f"[redacted — {len(content)} chars]"


def test_verbose_tool_short_content_still_redacted() -> None:
    """Even tiny read_file outputs are redacted to avoid leaking file contents in traces."""
    content = "tiny"
    result = _redact_content(content, tool_name="read_file")
    assert result == f"[redacted — {len(content)} chars]"


# ---------------------------------------------------------------------------
# Test 6 — _MAX_TRACE_CHARS hard cap respected on real fixture data
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_max_trace_chars_respected(runs: list[SimpleNamespace], mock_async_client: Any) -> None:
    """Each formatted trace string length is bounded by ``_MAX_TRACE_CHARS``."""
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
    """``TraceReport`` rejects ``is_offloaded=True`` without ``traces_path``."""
    with pytest.raises(Exception):
        TraceReport(
            project="p", fetched_at="", start_time="", end_time="",
            run_count=0, trace_count=0, trace_chars=0, error_count=0, total_cost=0.0,
            is_offloaded=True, traces_path=None,
        )


@pytest.mark.expect_exception
def test_model_validator_rejects_inline_without_traces() -> None:
    """``TraceReport`` rejects inline mode without a ``traces`` dict."""
    with pytest.raises(Exception):
        TraceReport(
            project="p", fetched_at="", start_time="", end_time="",
            run_count=0, trace_count=0, trace_chars=0, error_count=0, total_cost=0.0,
            is_offloaded=False, traces=None,
        )

@pytest.mark.expect_exception
def test_model_validator_rejects_both_set() -> None:
    """``TraceReport`` rejects having both ``traces`` and ``traces_path`` set."""
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

from src.tools.observability_eval_tools.summarize_traces import TraceSummaryList, TraceSummary, _summarize_traces_async


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
            parsed_output=TraceSummaryList(summaries=[_fake_trace_summary()])
        )
        return fake_response

    class _FakeMessages:
        async def parse(self, *args, **kwargs):
            return await slow_fake_parse(*args, **kwargs)

    class _FakeAnthropicClient:
        messages = _FakeMessages()

    # Replaces Anthropic client (_ASYNC_CLIENT.messages.parse) with slow_fake_parse
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