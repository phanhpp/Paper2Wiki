"""Anomaly regression tests — pytest-langsmith CI gate.

Each test case replays a failing production trace from an anomaly dataset and
asserts the target no longer exhibits the same failure.

Dataset naming: ``{scope}__rt_{run_type}__rn_{run_name}`` (no context suffix —
middleware spans are skipped at dataset-creation time).
Legacy datasets are matched by "failures" in the description.

Run:
    LANGSMITH_TEST_SUITE="paper2wiki regression" pytest tests/test_anomaly_regression.py
    LANGSMITH_TEST_SUITE="paper2wiki regression" pytest tests/test_anomaly_regression.py --langsmith-output

Cache LLM calls in CI:
    LANGSMITH_TEST_CACHE=tests/cassettes pytest tests/test_anomaly_regression.py
"""

from __future__ import annotations

import json
import os
import time

import pytest
import anthropic
from langsmith import Client, testing as t

from src.tools.evaluation_tools import build_target_function

_JUDGE_MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------------------
# Parametrize — one test case per (dataset, example) pair
# ---------------------------------------------------------------------------

def _anomaly_examples() -> list[tuple]:
    """Return [(dataset_name, example)] for all evaluable anomaly datasets.

    Returns an empty list (skips all tests) when LANGSMITH_API_KEY is missing
    or invalid so collection never hard-errors in CI.
    """
    if not os.getenv("LANGSMITH_API_KEY"):
        return []
    try:
        client = Client()
        params = []
        for ds in client.list_datasets():
            ds_desc = (ds.description or "").lower()
            print('ds description', ds_desc)
            # Support both new naming ("__rt_") and legacy anomaly datasets.
            if "__rt_" not in ds.name and "failures" not in ds_desc:
                continue
            for ex in client.list_examples(dataset_id=ds.id):
                ex_meta = ex.metadata or {}
                if ex_meta.get("context_name") is not None:
                    continue  # middleware span — no target available
                params.append(pytest.param(
                    ds.name, ex,
                    id=f"{ds.name}::{ex.id}",
                ))
        return params
    except Exception as exc:
        import warnings
        warnings.warn(f"test_anomaly_regression: skipping collection ({exc})")
        return []


def _judge_recovery(inputs: dict, outputs: dict, pass_criteria: str) -> int:
    """LLM judge: did the tool recover gracefully from the error? Returns 0 or 1."""
    judge = anthropic.Anthropic()
    resp = judge.messages.create(
        model=_JUDGE_MODEL,
        max_tokens=128,
        system=(
            f"You are evaluating whether a tool recovered gracefully from a hard error.\n"
            f"Pass criteria: {pass_criteria}\n"
            f"Score 1 if the output satisfies the pass criteria, 0 if it does not.\n"
            f'Reply with JSON only: {{"score": 0|1, "reason": "<10 words>"}}'
        ),
        messages=[{"role": "user", "content": json.dumps({"inputs": inputs, "outputs": outputs})}],
    )
    try:
        return int(json.loads(resp.content[0].text)["score"])
    except Exception:
        return 0


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
    pass_criteria: str = meta.get("pass_criteria", "No hard error")

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

    # Soft metrics — tracked in LangSmith, never fail CI on their own.
    t.log_feedback(key="local_latency_s", score=elapsed)

    # hard_error is the only signal suitable for a hard CI gate:
    # it's binary and deterministic. latency_spike, token_blowout, and
    # step_count_spike require run.latency / run.total_tokens / child_run_ids
    # which are only available via evaluate() — those are tracked there instead.
    if "hard_error" in errors:
        assert not outputs.get("error"), (
            f"[{dataset_name}] run_id={meta.get('run_id')} still erroring: "
            f"{outputs.get('error')}"
        )
        # LLM judge: was recovery graceful? Only meaningful when the tool didn't error.
        with t.trace_feedback():
            score = _judge_recovery(example.inputs, outputs, pass_criteria)
            t.log_feedback(key="recovery_quality", score=score)
