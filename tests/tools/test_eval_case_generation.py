"""Generated PR-gate cases must never block a merge on something non-deterministic.

The trace-analysis skill turns a tool hard error into a candidate `pr_gate_cases.json`
entry. That generator used to hardcode ``"type": "regression"``, so a `fetch_arxiv`
failure became a **blocking** case scored at 1.00 (`REGRESSION_THRESHOLDS`) even though
the tool needs the network — and the anomaly that produced it was an HTTP 429, so it
would have flaked on exactly the error that created it.

The agent was not at fault: the value arrived pre-filled from this tool, and the skill's
template and worked example both showed `regression` too. The fix is here, where the
decision actually is.

No network, no LLM — the report is hand-built.
"""

from __future__ import annotations

import pytest

from src.tools.observability_eval_tools.anomaly_detection import (
    AnomalyReport,
    AnomalySignal,
    FailedSpan,
)
from src.tools.observability_eval_tools.create_eval_datasets import (
    _TOOL_EXECUTION,
    _generate_PR_cases,
)


def _report(tool_name: str) -> AnomalyReport:
    """One tool hard error — the only shape that yields a gate case."""
    span = FailedSpan(
        id=f"{tool_name}-span-id-0001",
        run_name=tool_name,
        run_type="tool",
        flow="wiki_ingestion",
        errors=["hard_error"],
        signals=[f"hard_error:<boom> in {tool_name}"],
        inputs={"query": "x"},
        outputs={},
    )
    return AnomalyReport(
        total_runs_analyzed=1,
        anomalous_run_count=1,
        anomalies=[AnomalySignal(
            trace_id=f"trace-{tool_name}",
            errors=["hard_error"],
            signals=[f"hard_error:<boom> in {tool_name}"],
            failed_spans=[span],
        )],
    )


# --- the classification decides whether a case can block ------------------------

@pytest.mark.unit
def test_every_registered_tool_has_an_execution_kind():
    """Adding a tool must include classifying it. **This test is the reminder.**

    An unclassified tool falls to "network" → capability, so its failures can never
    become blocking gate cases. That is the safe direction, but it is silent: a
    genuinely local tool would quietly stop hardening the gate and nothing would say so.
    """
    from src.tools import all_tools

    missing = {t.name for t in all_tools} - set(_TOOL_EXECUTION)
    assert not missing, (
        f"Classify these in _TOOL_EXECUTION: {sorted(missing)}.\n"
        "  'local'   — no network and no API key; may become a blocking regression case\n"
        "  'web'     — needs a search-provider key (run_gate skips it without one)\n"
        "  'network' — any other external call: an API, or an LLM"
    )


@pytest.mark.unit
def test_execution_kinds_are_valid():
    assert set(_TOOL_EXECUTION.values()) <= {"local", "web", "network"}


@pytest.mark.unit
def test_quality_mode_tools_are_classified_too(monkeypatch):
    """`fetch_arxiv` / `parse_pdf_docling` only register in quality mode.

    Without this, the check above passes in `fast` mode while leaving them unclassified.
    """
    for name in ("fetch_arxiv", "parse_pdf_docling"):
        assert name in _TOOL_EXECUTION, f"{name} (quality-mode tool) is unclassified"


# --- what the generator emits ---------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("tool_name", sorted(
    n for n, k in _TOOL_EXECUTION.items() if k != "local"
))
def test_non_local_tools_never_produce_a_blocking_case(tool_name):
    """The regression: a network tool must not become a gate-blocking case."""
    cases = _generate_PR_cases(_report(tool_name))

    assert len(cases) == 1
    assert cases[0]["type"] == "capability", (
        f"{tool_name} needs the network or a key, so a regression case would block "
        "every PR when it flakes"
    )


@pytest.mark.unit
@pytest.mark.parametrize("tool_name", sorted(
    n for n, k in _TOOL_EXECUTION.items() if k == "local"
))
def test_local_tools_do_produce_a_blocking_case(tool_name):
    """The other half: a local failure *should* harden the gate immediately.

    Defaulting everything to capability would be safe but useless — nothing would ever
    block, which defeats the point of promoting a fix into durable coverage.
    """
    cases = _generate_PR_cases(_report(tool_name))
    assert cases[0]["type"] == "regression"


@pytest.mark.unit
def test_web_tools_are_marked_requires_web_provider():
    """Without this flag a generated web case fails on a machine with no key.

    `run_gate.py:84` skips cases carrying it; the generator never set it before.
    """
    case = _generate_PR_cases(_report("web_search"))[0]
    assert case.get("requires_web_provider") is True


@pytest.mark.unit
def test_unknown_tool_falls_back_to_capability():
    """Fail-safe: a tool nobody classified must not be able to block a merge."""
    case = _generate_PR_cases(_report("some_brand_new_tool"))[0]
    assert case["type"] == "capability"


@pytest.mark.unit
def test_case_id_matches_its_type():
    """An id saying "regression_" while the type is capability misleads the reviewer."""
    for tool_name in ("compute_sha256", "web_search"):
        case = _generate_PR_cases(_report(tool_name))[0]
        assert case["id"].startswith(case["type"] + "_")


@pytest.mark.unit
@pytest.mark.parametrize("tool_name", ["compute_sha256", "web_search"])
def test_review_note_marks_the_case_incomplete(tool_name):
    """`_review` exists to say "no assertion yet" — nothing more.

    It does not restate `type`; that is in the same object. It names
    `expect_error_contains` because bare `expect_error` passes on any exception, so a
    reviewer reaching for the obvious field would write a case that cannot fail.
    """
    case = _generate_PR_cases(_report(tool_name))[0]

    assert not any(k.startswith("expect_") for k in case), "no assertion should be set yet"
    assert "expect_error_contains" in case["_review"]
    assert case["type"] not in case["_review"], "type is already a field; don't repeat it"
