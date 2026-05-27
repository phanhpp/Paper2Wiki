"""Anomaly regression tests — pytest-langsmith CI gate.

Each test case replays a failing production trace from an anomaly dataset and
asserts the target no longer exhibits the same failure.

Dataset naming: ``{scope}__rt_{run_type}__rn_{run_name}`` (no context suffix —
middleware spans are skipped at dataset-creation time).

Run:
    LANGSMITH_TEST_SUITE="paper2wiki regression" pytest tests/test_anomaly_regression.py
    LANGSMITH_TEST_SUITE="paper2wiki regression" pytest tests/test_anomaly_regression.py --langsmith-output

Cache LLM calls in CI:
    LANGSMITH_TEST_CACHE=tests/cassettes pytest tests/test_anomaly_regression.py
"""

from __future__ import annotations

import re
import time

import pytest
from langsmith import Client, testing as t

from src.tools.anomaly_detection import _SPIKE_MULTIPLIER
from src.tools.evaluation_tools import build_target_function


# ---------------------------------------------------------------------------
# Parametrize — one test case per (dataset, example) pair
# ---------------------------------------------------------------------------

def _anomaly_examples() -> list[tuple]:
    """Return [(dataset_name, example)] for all evaluable anomaly datasets."""
    client = Client()
    params = []
    for ds in client.list_datasets():
        meta = ds.metadata or {}
        if "__rt_" not in ds.name:
            continue
        if meta.get("context_name") is not None:
            continue  # middleware span — no target available
        for ex in client.list_examples(dataset_id=ds.id):
            params.append(pytest.param(
                ds.name, ex,
                id=f"{ds.name}::{ex.id}",
            ))
    return params


# ---------------------------------------------------------------------------
# Regression test
# ---------------------------------------------------------------------------

@pytest.mark.langsmith
@pytest.mark.asyncio
@pytest.mark.parametrize("dataset_name,example", _anomaly_examples())
async def test_anomaly_regression(dataset_name: str, example) -> None:
    """Re-run the failing span and assert it no longer exhibits the anomaly signal."""
    meta = example.metadata or {}
    errors: list[str] = meta.get("errors", [])
    signals: list[str] = meta.get("signals", [])

    t.log_inputs(example.inputs)
    t.log_reference_outputs(example.outputs)

    target = build_target_function(dataset_name)

    start = time.perf_counter()
    try:
        outputs = await target(example.inputs)
    except Exception as exc:
        outputs = {"error": str(exc)}
    elapsed = time.perf_counter() - start

    t.log_outputs(outputs)

    # --- hard gates — AssertionError blocks CI ---

    if "hard_error" in errors:
        assert not outputs.get("error"), (
            f"[{dataset_name}] run_id={meta.get('run_id')} still erroring: "
            f"{outputs.get('error')}"
        )

    # --- soft gates — logged to LangSmith UI, never fail CI ---

    for sig in signals:
        if sig.startswith("latency_spike"):
            m = re.search(r"_vs_median_([\d.]+)s", sig)
            if m:
                threshold = _SPIKE_MULTIPLIER * float(m.group(1))
                t.log_feedback(
                    key="latency_not_spiking",
                    score=1 if elapsed <= threshold else 0,
                    comment=f"{elapsed:.1f}s vs threshold {threshold:.1f}s",
                )

        elif sig.startswith("token_blowout"):
            # total_tokens requires run tree access — log as pending
            t.log_feedback(
                key="token_blowout_check",
                score=0,
                comment="token count not available outside evaluate(); check LangSmith UI",
            )

        elif sig.startswith("step_count_spike"):
            # step count is a trace-level metric — soft note only
            t.log_feedback(
                key="step_count_noted",
                score=1,
                comment=f"original signal: {sig}",
            )
