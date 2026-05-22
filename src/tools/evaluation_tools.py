"""LangSmith evaluation tools.

Pipeline (after ``detect_anomalies_async``):

    create_datasets_from_anomaly_report(report)  # see evaluation_datasets.py

    build_evaluators_for_signals(signals)
        → returns the right mix of evaluators for a set of anomaly signals:
            hard_error                               → no_hard_error (code) + LLM judge (recovery quality)
            latency_spike                            → category recorded in metadata
            token_blowout                            → category recorded in metadata
            step_count_spike                         → LLM judge (step necessity)
            output quality judging                   → opt-in: build_output_quality_judge() and
                                                       append to evaluators before calling run_evaluate

    run_evaluate(dataset_name, target, evaluators)
        → caller supplies the target callable:
            flow-scoped  dataset → pass agent.ainvoke
            tool-scoped  dataset → pass the specific tool.ainvoke
            llm-scoped   dataset → pass the specific llm.ainvoke
        → returns ExperimentResults (.url for LangSmith UI, .experiment_name to reference later)

Composite / summary / pairwise:
    apply_composite_scores(client, experiment_name, weights)
        → re-fetch settled results via get_experiment_results(), compute weighted sum across
          individual eval scores, write back as "overall_health" feedback key. Call AFTER
          evaluate() finishes. Default weights: no_hard_error=1.0.

    build_pass_rate_evaluator(key)
        → returns a summary_evaluator for experiment-level pass rate. Pass to
          evaluate(summary_evaluators=[...]). Fires once after all runs settle.

    Pairwise — evaluate(("exp-a", "exp-b"), evaluators=[judge_fn]) — only useful once you
               have a before/after fix on the same dataset.

TODO: Trajectory evaluation
    Extract the sequence of tool calls from a trace and evaluate whether the agent
    followed the expected trajectory (e.g. always calls quick_wiki_integrity_check
    before committing). Needs:
    - A trajectory dataset builder that walks child runs in order
    - An evaluator that checks call order / presence of required steps
    - Likely uses agentevals or a custom LLM judge with the expected trajectory as reference
    Will be implemented once the trace format for trajectories is confirmed.
"""

from __future__ import annotations

import json
import math
from typing import Callable, Sequence

import anthropic
from langsmith import Client
from langsmith.evaluation import evaluate
from langsmith.evaluation.evaluator import EvaluationResult
from langsmith.schemas import Run, Example

from src.tools.anomaly_detection import AnomalyError
from src.tools.evaluation_datasets import create_datasets_from_anomaly_report

_JUDGE_MODEL = "claude-haiku-4-5-20251001"

_DEFAULT_COMPOSITE_WEIGHTS = {
    "no_hard_error": 1.0,
}


# ---------------------------------------------------------------------------
# Code evaluators — deterministic, no LLM call
# ---------------------------------------------------------------------------

def no_hard_error(outputs: dict) -> dict:
    """Score 1 if the run produced no error field, 0 if it did.

    Use for ``hard_error`` signals. Checks the outputs dict directly — if the
    tool raised an exception, the output typically contains an 'error' key or
    is empty.
    """
    has_error = bool(outputs.get("error") or outputs.get("Error"))
    return {"key": "no_hard_error", "score": 0 if has_error else 1}


# ---------------------------------------------------------------------------
# LLM-as-judge evaluators
# ---------------------------------------------------------------------------

def build_llm_judge(signal: str, pass_criteria: str) -> Callable:
    """Return an LLM-as-judge evaluator scoped to a specific signal and pass criteria.

    Use for signals that require semantic understanding:
    - Did the agent recover gracefully from the error?
    - Did the output make sense given the input?
    - Were the extra steps in a step_count_spike actually necessary?

    The judge prompt is scoped to the exact failure pattern so it doesn't
    hallucinate a generic quality rubric.

    Args:
        signal:        The anomaly signal string (e.g. ``"hard_error:HTTPError 429"``).
        pass_criteria: From ``example.metadata["pass_criteria"]`` — describes what
                       correct behaviour looks like.

    Returns:
        A LangSmith-compatible evaluator function.
    """
    judge = anthropic.Anthropic()
    eval_key = f"llm_judge_{signal.split(':')[0]}"  # e.g. "llm_judge_hard_error"
    system_prompt = (
        f"You are evaluating an AI agent's response for a known failure pattern.\n"
        f"Failure signal: {signal}\n"
        f"Pass criteria: {pass_criteria}\n"
        f"Score 1 if the output satisfies the pass criteria, 0 if it does not.\n"
        f'Reply with JSON only: {{"score": 0|1, "reason": "<10 words>"}}'
    )

    def evaluator_fn(inputs: dict, outputs: dict) -> dict:
        resp = judge.messages.create(
            model=_JUDGE_MODEL,
            max_tokens=128,
            system=system_prompt,
            messages=[{"role": "user", "content": json.dumps({"inputs": inputs, "outputs": outputs})}],
        )
        try:
            result = json.loads(resp.content[0].text)
            return {"key": eval_key, "score": result["score"], "comment": result.get("reason", "")}
        except Exception:
            return {"key": eval_key, "score": 0, "comment": "parse error"}

    evaluator_fn.__name__ = eval_key
    return evaluator_fn


def build_output_quality_judge(signals: list[str], pass_criteria: str) -> Callable:
    """Return a general LLM judge that scores whether the *output* makes sense.

    Unlike signal-specific judges, this always fires regardless of which anomaly
    triggered. Code evaluators can only check numbers — this judge checks whether
    the output content is coherent and correct given the input.

    Args:
        signals:       All signals from the run (used to frame the context).
        pass_criteria: From ``example.metadata["pass_criteria"]``.

    Returns:
        A LangSmith-compatible evaluator with key ``"llm_judge_output_quality"``.
    """
    judge = anthropic.Anthropic()
    context = "; ".join(signals) if signals else "anomaly"
    system_prompt = (
        f"You are evaluating an AI agent's output quality after an anomaly was detected.\n"
        f"Anomaly signals: {context}\n"
        f"Pass criteria: {pass_criteria}\n"
        f"Score 1 if the output is coherent and correct given the input, 0 if it is not.\n"
        f'Reply with JSON only: {{"score": 0|1, "reason": "<10 words>"}}'
    )

    def evaluator_fn(inputs: dict, outputs: dict) -> dict:
        resp = judge.messages.create(
            model=_JUDGE_MODEL,
            max_tokens=128,
            system=system_prompt,
            messages=[{"role": "user", "content": json.dumps({"inputs": inputs, "outputs": outputs})}],
        )
        try:
            result = json.loads(resp.content[0].text)
            return {"key": "llm_judge_output_quality", "score": result["score"], "comment": result.get("reason", "")}
        except Exception:
            return {"key": "llm_judge_output_quality", "score": 0, "comment": "parse error"}

    evaluator_fn.__name__ = "llm_judge_output_quality"
    return evaluator_fn


# ---------------------------------------------------------------------------
# Evaluator selection
# ---------------------------------------------------------------------------

def build_evaluators_for_errors(
    errors: list[AnomalyError],
    signals: list[str],
    pass_criteria: str,
) -> list[Callable]:
    """Return evaluators for explicit anomaly categories.

    Rules:
    - ``hard_error``       → ``no_hard_error`` (code) + LLM judge for recovery quality
    - ``latency_spike``    → category is already captured in the example metadata
    - ``token_blowout``    → category is already captured in the example metadata
    - ``step_count_spike`` → LLM judge for step necessity

    Output quality judging is opt-in: call ``build_output_quality_judge`` separately
    and append it to the returned list when you want to score output correctness.

    Args:
        errors:        Typed anomaly categories from ``AnomalySignal.errors``.
        signals:       Detailed signal strings from ``AnomalySignal.signals``.
        pass_criteria: From ``example.metadata["pass_criteria"]``.

    Returns:
        Deduplicated list of evaluator callables ready to pass to ``run_evaluate``.
    """
    evals: list[Callable] = []
    seen_keys: set[str] = set()

    def _add(fn: Callable) -> None:
        if fn.__name__ not in seen_keys:
            seen_keys.add(fn.__name__)
            evals.append(fn)

    signal_by_error = {signal.split(":", 1)[0]: signal for signal in signals}
    for error in errors:
        signal = signal_by_error.get(error, error)
        if error == "hard_error":
            _add(no_hard_error)
            _add(build_llm_judge(signal, pass_criteria))  # was recovery graceful?
        elif error == "step_count_spike":
            _add(build_llm_judge(signal, pass_criteria))  # were extra steps necessary?

    return evals


def build_evaluators_for_signals(signals: list[str], pass_criteria: str) -> list[Callable]:
    """Backward-compatible wrapper that derives categories from signal strings."""
    errors = [signal.split(":", 1)[0] for signal in signals]
    return build_evaluators_for_errors(errors, signals, pass_criteria)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Composite scoring — call AFTER evaluate() settles
# ---------------------------------------------------------------------------

def apply_composite_scores(
    client: Client,
    experiment_name: str,
    weights: dict[str, float] | None = None,
) -> int:
    """Compute a weighted ``overall_health`` score per run and write it back to LangSmith.

    Call after ``run_evaluate()`` finishes (or after ``evaluate(...).wait()``).
    Re-fetches settled results via ``get_experiment_results()``, computes a
    weighted sum of individual eval scores, and creates a new feedback key
    ``"overall_health"`` on each run so it appears in the LangSmith UI.

    Args:
        client:          LangSmith Client.
        experiment_name: From ``ExperimentResults.experiment_name``.
        weights:         Dict of ``{eval_key: weight}`` — should sum to 1.0.
                         Defaults to ``_DEFAULT_COMPOSITE_WEIGHTS``.

    Returns:
        Number of runs that received a composite score.
    """
    w = weights or _DEFAULT_COMPOSITE_WEIGHTS
    settled = client.get_experiment_results(name=experiment_name)

    scored = 0
    for example_with_runs in settled["examples_with_runs"]:
        for run in example_with_runs.runs:
            fb = run.feedback_stats or {}
            # require all weight keys to be present — skip partial runs
            if not set(w.keys()).issubset(fb.keys()):
                continue
            total = sum(
                fb[key].get("avg", 0) * weight
                for key, weight in w.items()
                if fb[key].get("n", 0) > 0
            )
            if not math.isnan(total):
                client.create_feedback(
                    run_id=run.id,
                    key="overall_health",
                    score=float(total),
                )
                scored += 1

    return scored


# ---------------------------------------------------------------------------
# Summary evaluator — experiment-level pass rate
# ---------------------------------------------------------------------------

def build_pass_rate_evaluator(key: str = "no_hard_error") -> Callable:
    """Return a summary_evaluator that computes experiment-level pass rate for a metric.

    Pass to ``evaluate(summary_evaluators=[build_pass_rate_evaluator(...)])``.
    Fires once after all runs settle, receiving all runs and examples at once.

    Args:
        key: The feedback key to compute pass rate for (e.g. ``"no_hard_error"``).

    Returns:
        A summary evaluator function compatible with LangSmith's ``summary_evaluators`` param.
    """
    def pass_rate(runs: Sequence[Run], examples: Sequence[Example]) -> EvaluationResult:
        passed = sum(
            1 for r in runs
            if (r.feedback_stats or {}).get(key, {}).get("avg", 0) == 1
        )
        return EvaluationResult(
            key=f"{key}_pass_rate",
            score=passed / len(runs) if runs else 0,
        )

    pass_rate.__name__ = f"{key}_pass_rate"
    return pass_rate


# ---------------------------------------------------------------------------
# Run evaluation
# ---------------------------------------------------------------------------
# todo: build target function based on dataset scope
def build_target_function(dataset_name: str) -> Callable:
    """Build a target function based on dataset scope."""

def run_evaluate(
    dataset_name: str,
    target: Callable,
    evaluators: list[Callable],
    *,
    summary_evaluators: list[Callable] | None = None,
    experiment_prefix: str | None = None,
    metadata_filter: dict | None = None,
    client: Client | None = None,
):
    """Run a LangSmith evaluation against a dataset.

    The caller supplies ``target`` — the callable invoked on each example's inputs.
    Choose based on the dataset scope:
    - Flow-scoped dataset  (``paper2wiki_{flow}``)        → ``agent.ainvoke``
    - Tool-scoped dataset  (``paper2wiki_{flow}_{name}``) → ``tool.ainvoke``
    - LLM-scoped dataset   (``paper2wiki_{flow}_llm``)    → ``llm.ainvoke``

    After the returned ExperimentResults settles, call ``apply_composite_scores``
    to write an ``overall_health`` weighted score per run.

    Args:
        dataset_name:       LangSmith dataset to evaluate against.
        target:             Callable ``(inputs: dict) -> dict``.
        evaluators:         List of evaluator functions from ``build_evaluators_for_signals``.
        summary_evaluators: Optional experiment-level evaluators from ``build_pass_rate_evaluator``.
                            Fires once after all runs complete.
        experiment_prefix:  Optional prefix for the experiment name in the LangSmith UI.
        metadata_filter:    If set, restricts examples by metadata (e.g. ``{"flow": "wiki-ingestion"}``).
        client:             Optional pre-constructed LangSmith Client for test injection.

    Returns:
        ``ExperimentResults`` — call ``.url`` for the LangSmith UI link,
        ``.experiment_name`` to pass to ``apply_composite_scores`` later.
    """
    ls = client or Client()

    if metadata_filter:
        data = list(ls.list_examples(dataset_name=dataset_name, metadata=metadata_filter))
    else:
        data = dataset_name

    return evaluate(
        target,
        data=data,
        evaluators=evaluators,
        summary_evaluators=summary_evaluators or [],
        experiment_prefix=experiment_prefix,
        client=ls,
    )
