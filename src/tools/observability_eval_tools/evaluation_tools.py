"""LangSmith evaluation tools.

Pipeline (after ``detect_anomalies_async``):

    create_datasets_from_anomaly_report(report)  # see create_eval_datasets.py

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
import re
from typing import Callable, Sequence

import anthropic
from langsmith import Client
from langsmith.evaluation import aevaluate
from langsmith.evaluation.evaluator import EvaluationResult
from langsmith.schemas import Run, Example

from src.tools.observability_eval_tools.anomaly_detection import AnomalyError, _SPIKE_MULTIPLIER
from src.tools.observability_eval_tools.create_eval_datasets import create_datasets_from_anomaly_report
from src.tools import all_tools
from src.agents.llms import set_up_llms
from langchain_core.tools import tool

_JUDGE_MODEL = "claude-haiku-4-5-20251001"

_DEFAULT_COMPOSITE_WEIGHTS = {
    "no_hard_error": 1.0,
}


# ---------------------------------------------------------------------------
# Code evaluators — deterministic, no LLM call
# ---------------------------------------------------------------------------

def no_hard_error(run: Run) -> dict:
    """Score 1 if the run produced no error, 0 if it did.

    Use for ``hard_error`` signals. Reads ``run.error`` — the authoritative
    field set by LangSmith when a tool raises. Checking ``outputs`` is
    unreliable because a crashed tool returns ``{}`` with no error key.
    """
    return {"key": "no_hard_error", "score": 0 if run.error is not None else 1}


def build_latency_evaluator(signal: str) -> Callable:
    """Code evaluator for ``latency_spike`` — passes if the new run's latency stays below 3× baseline.

    Parses the baseline median from the signal string
    (e.g. ``latency_spike:123.1s_vs_median_40.5s``) so the threshold matches
    exactly what triggered the anomaly.  Reads ``run.latency`` from the
    LangSmith Run object supplied by ``evaluate()``.
    """
    m = re.search(r"_vs_median_([\d.]+)s", signal)
    median_latency = float(m.group(1)) if m else None
    threshold = _SPIKE_MULTIPLIER * median_latency if median_latency is not None else None

    def latency_not_spiking(run: Run) -> dict:
        latency = run.latency
        if latency is None:
            return {"key": "latency_not_spiking", "score": 0, "comment": "missing latency"}
        if threshold is None:
            return {"key": "latency_not_spiking", "score": 1, "comment": "no baseline"}
        passed = latency <= threshold
        return {
            "key": "latency_not_spiking",
            "score": 1 if passed else 0,
            "comment": f"{latency:.1f}s vs threshold {threshold:.1f}s",
        }

    latency_not_spiking.__name__ = "latency_not_spiking"
    return latency_not_spiking


def build_token_evaluator(signal: str) -> Callable:
    """Code evaluator for ``token_blowout`` — passes if the new run's token count stays below 3× baseline.

    Parses the baseline median from the signal string
    (e.g. ``token_blowout:5000_vs_median_1200``) so the threshold is anchored
    to the same baseline that triggered the anomaly.  Reads ``run.total_tokens``
    from the LangSmith Run object supplied by ``evaluate()``.
    """
    m = re.search(r"_vs_median_([\d.]+)$", signal)
    median_tokens = float(m.group(1)) if m else None
    threshold = _SPIKE_MULTIPLIER * median_tokens if median_tokens is not None else None

    def tokens_not_blown(run: Run) -> dict:
        tokens = run.total_tokens
        if not tokens:
            return {"key": "tokens_not_blown", "score": 1, "comment": "no tokens recorded"}
        if threshold is None:
            return {"key": "tokens_not_blown", "score": 1, "comment": "no baseline"}
        passed = tokens <= threshold
        return {
            "key": "tokens_not_blown",
            "score": 1 if passed else 0,
            "comment": f"{tokens} tokens vs threshold {threshold:.0f}",
        }

    tokens_not_blown.__name__ = "tokens_not_blown"
    return tokens_not_blown


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


# ---------------------------------------------------------------------------
# Evaluator selection
# ---------------------------------------------------------------------------

def build_evaluators_for_errors(
    dataset_name: str,
    metadata_filter: dict | None = None,
    client: Client | None = None,
) -> list[Callable]:
    """Return evaluators for explicit anomaly categories.

    Rules:
    - ``hard_error``       → ``no_hard_error`` (code) + LLM judge for recovery quality
    - ``latency_spike``    → ``latency_not_spiking`` code evaluator (checks ``run.latency``)
    - ``token_blowout``    → ``tokens_not_blown`` code evaluator (checks ``run.total_tokens``)
    - ``step_count_spike`` → LLM judge for step necessity

    Output quality judging is opt-in: call ``build_output_quality_judge`` separately
    and append it to the returned list when you want to score output correctness.

    Args:
        dataset_name: LangSmith dataset name created by ``create_eval_datasets.py``.
    Returns:
        Deduplicated list of evaluator callables ready to pass to ``run_evaluate``.
    """

    # get the examples from the dataset
    ls = client or Client()

    if metadata_filter:
        examples = ls.list_examples(dataset_name=dataset_name, metadata=metadata_filter)
    else:
        examples = ls.list_examples(dataset_name=dataset_name)

    # Aggregate anomaly metadata across examples in this dataset.
    errors: set[AnomalyError] = set()
    signal_by_error: dict[str, str] = {}
    pass_criteria_parts: list[str] = []
    for ex in examples:
        meta = ex.metadata or {}
        for error in meta.get("errors", []):
            if isinstance(error, str):
                errors.add(error)  # type: ignore[arg-type]
        for signal in meta.get("signals", []):
            if not isinstance(signal, str):
                continue
            key = signal.split(":", 1)[0]
            signal_by_error.setdefault(key, signal)
        criteria = meta.get("pass_criteria")
        if isinstance(criteria, str) and criteria.strip():
            pass_criteria_parts.append(criteria)

    pass_criteria = (
        " ; ".join(dict.fromkeys(pass_criteria_parts))
        if pass_criteria_parts
        else "No hard error"
    )

    evals: list[Callable] = []
    seen_keys: set[str] = set()

    def _add(fn: Callable) -> None:
        if fn.__name__ not in seen_keys:
            seen_keys.add(fn.__name__)
            evals.append(fn)

    for error in ("hard_error", "latency_spike", "token_blowout", "step_count_spike"):
        if error not in errors:
            continue
        signal = signal_by_error.get(error, error)
        if error == "hard_error":
            _add(no_hard_error)
            _add(build_llm_judge(signal, pass_criteria))  # was recovery graceful?
        elif error == "latency_spike":
            _add(build_latency_evaluator(signal))
        elif error == "token_blowout":
            _add(build_token_evaluator(signal))
        elif error == "step_count_spike":
            _add(build_llm_judge(signal, pass_criteria))  # were extra steps necessary?

    return evals




# ---------------------------------------------------------------------------
# Composite scoring — call AFTER evaluate() settles
# ---------------------------------------------------------------------------
# TODO: test this
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
# TODO: test this
def build_pass_rate_evaluator(key: str = "no_hard_error") -> Callable:
    """Return a summary_evaluator that computes experiment-level pass rate.

    Pass to ``evaluate(summary_evaluators=[build_pass_rate_evaluator(...)])``.
    Fires once after all runs settle, receiving all outputs at once.

    Args:
        key: Label for the metric key written to LangSmith (e.g. ``"no_hard_error"``).

    Returns:
        A summary evaluator function compatible with LangSmith's ``summary_evaluators`` param.
    """
    def pass_rate(outputs: list[dict], runs: list[Run]) -> dict:
        passed = sum(
            1 for o, r in zip(outputs, runs)
            if not r.error and not (o.get("error") or o.get("Error"))
        )
        return {"key": f"{key}_pass_rate", "score": passed / len(outputs) if outputs else 0}

    pass_rate.__name__ = f"{key}_pass_rate"
    return pass_rate


# ---------------------------------------------------------------------------
# Run evaluation
# ---------------------------------------------------------------------------
def build_target_function(dataset_name: str, client: Client | None = None) -> Callable:
    """Build an async target callable by reading the dataset's metadata from LangSmith.

    Dispatch rules (confirmed against real trace data):
    - ``run_type=tool``,  ``context_name=None``  → ``tool.ainvoke``  (e.g. fetch_arxiv)
    - ``run_type=llm``,   ``context_name=None``  → ``llm.ainvoke``   (standalone ChatAnthropic)
    - ``run_type=chain``, ``context_name=None``  → ``agent.ainvoke`` (root supervisor, rare)
    - ``context_name`` is not None               → raises ValueError  (middleware span, skip)

    Sandbox tools (save_sandbox_output, get_sandbox_state, list_sandbox_files) are not
    registered in ``all_tools`` — they raise ValueError until sandbox tool lookup is wired up.

    Args:
        dataset_name: LangSmith dataset name created by ``create_eval_datasets.py``.
        client:       Optional pre-constructed LangSmith Client.

    Returns:
        Async callable ``(inputs: dict) -> dict`` ready for ``evaluate(target=...)``.

    Raises:
        ValueError: If the dataset targets a middleware span, an unregistered tool, or
                    an unknown run_type.
    """
    ls = client or Client()
    meta = (ls.read_dataset(dataset_name=dataset_name).metadata or {})
    run_type = meta.get("run_type")
    run_name = meta.get("run_name")
    ctx = meta.get("context_name")

    if ctx is not None:
        raise ValueError(
            f"Dataset '{dataset_name}' targets a middleware span (context_name={ctx!r}) — "
            "skip evaluation for middleware spans until target isolation is implemented."
        )

    if run_type == "tool":
        tool_map = {t.name: t for t in all_tools}
        t = tool_map.get(run_name)
        if t is None:
            import pytest
            pytest.skip(
                f"Tool {run_name!r} is not registered in all_tools "
                f"(sandbox tools are not yet supported as evaluation targets)."
            )

        async def target(inputs: dict) -> dict:
            result = await t.ainvoke(inputs)
            return result if isinstance(result, dict) else {"output": result}

        target.__name__ = f"target_{run_name}"
        return target

    elif run_type == "llm":
        llm = set_up_llms("claude-haiku-4-5-20251001")

        async def target(inputs: dict) -> dict:
            response = await llm.ainvoke(inputs.get("messages", inputs))
            content = response.content if hasattr(response, "content") else str(response)
            return {"output": content}

        target.__name__ = f"target_{run_name}"
        return target

    elif run_type == "chain":
        from src.agents.agent import create_supervisor  # lazy to avoid circular import

        async def target(inputs: dict) -> dict:
            agent = await create_supervisor()
            result = await agent.ainvoke(inputs)
            return result if isinstance(result, dict) else {"output": str(result)}

        target.__name__ = "target_agent"
        return target

    else:
        raise ValueError(
            f"Unknown run_type={run_type!r} in metadata for dataset '{dataset_name}'"
        )

# call list_datasets() to get the dataset name + may add description for agent to know which to use
# for now skip those with context_name is not None
@tool()
async def run_evaluate(
    dataset_name: str,
    *,
    experiment_prefix: str | None = None,
    metadata_filter: dict | None = None,
):
    """Run an async LangSmith evaluation against a dataset.

    The caller supplies ``target`` — the callable invoked on each example's inputs.
    Choose based on the dataset scope:
    - Flow-scoped dataset  (``paper2wiki_{flow}``)        → ``agent.ainvoke``
    - Tool-scoped dataset  (``paper2wiki_{flow}_{name}``) → ``tool.ainvoke``
    - LLM-scoped dataset   (``paper2wiki_{flow}_llm``)    → ``llm.ainvoke``

    After the returned ExperimentResults settles, call ``apply_composite_scores``
    to write an ``overall_health`` weighted score per run.

    Args:
        dataset_name:       LangSmith dataset to evaluate against.
        experiment_prefix:  Optional prefix for the experiment name in the LangSmith UI.
        metadata_filter:    If set, restricts examples by metadata (e.g. ``{"flow": "wiki-ingestion"}``).

    Returns:
        ``ExperimentResults`` — call ``.url`` for the LangSmith UI link,
        ``.experiment_name`` to pass to ``apply_composite_scores`` later.
    """
    ls = Client()
    effective_filter = dict(metadata_filter or {})
    effective_filter.setdefault("context_name", None)

    if not ls.has_dataset(dataset_name=dataset_name):
        raise ValueError(f"Dataset {dataset_name} not found")

    evaluators = build_evaluators_for_errors(dataset_name, metadata_filter=effective_filter, client=ls)
    target = build_target_function(dataset_name, client=ls)

    data = (
        list(ls.list_examples(dataset_name=dataset_name, metadata=effective_filter))
        if effective_filter
        else dataset_name
    )

    return await aevaluate(
        target,
        data=data,
        evaluators=evaluators,
        experiment_prefix=experiment_prefix,
        client=ls,
    )
