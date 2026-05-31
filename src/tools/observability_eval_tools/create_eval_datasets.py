"""LangSmith dataset creation from anomaly reports.

Called after ``detect_anomalies_async()``:

    create_datasets_from_anomaly_report(report)
        → datasets scoped by flow / run name / middleware context
        → appends new examples, skips already-pushed run_ids
"""

from __future__ import annotations

import ast
import json
import re
from collections import defaultdict
from typing import Any

from langsmith import Client

from src.tools.observability_eval_tools.anomaly_detection import AnomalyReport, FailedSpan
from langchain_core.tools import tool

_SCOPE_BY_RUN_TYPE: dict[str, str] = {
    "tool":  "tool",
    "chain": "flow",
    "llm":   "llm",
}

_GENERIC_WRAPPER_NAMES = {"model", "tools"}


def _safe_dataset_component(value: str | None) -> str:
    """Return a LangSmith-dataset-safe name component."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", value or "").strip("_")
    return cleaned or "unknown"


def _dataset_name(span: FailedSpan) -> str:
    """Derive a dataset name from scope + target signature.

    Scoping rules:
    - flow-backed spans → ``{flow}``
    - generic wrapper names ``model``/``tools`` → ``{context_name}``
      when parser context is available
    - other flowless spans → ``global``

    Then append a single target signature so each component appears once:
    ``__rt_<run_type>__rn_<run_name>__ctx_<context|none>``.
    """
    run_name = _safe_dataset_component(span.run_name)

    if span.flow:
        scope = _safe_dataset_component(span.flow)
    elif span.run_name in _GENERIC_WRAPPER_NAMES:
        scope = _safe_dataset_component(span.context_name or "middleware")
    else:
        scope = "global"

    run_type = _safe_dataset_component(span.run_type or "unknown")
    return f"{scope}__rt_{run_type}__rn_{run_name}"


def _dataset_description(span: FailedSpan) -> str:
    """Create a precise dataset description for automatic target selection."""
    context_value = span.context_name if span.context_name is not None else "None"
    return (
        "Use this dataset when evaluating spans with "
        f"run_type={span.run_type or 'unknown'}, "
        f"run_name={span.run_name}, "
        f"context_name={context_value}."
    )


def _normalize_example_inputs(raw_inputs: dict[str, Any]) -> dict[str, Any]:
    """Normalize legacy wrapped/stringified tool inputs for replay."""
    if not isinstance(raw_inputs, dict):
        return {"input": raw_inputs}

    wrapped = raw_inputs.get("input")
    if "input" not in raw_inputs or len(raw_inputs) != 1:
        return raw_inputs
    if isinstance(wrapped, dict):
        return wrapped
    if not isinstance(wrapped, str):
        return raw_inputs

    text = wrapped.strip()
    for parser in (json.loads, ast.literal_eval):
        try:
            parsed = parser(text)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
    return raw_inputs


@tool()
def create_datasets_from_anomaly_report(
    report: AnomalyReport,
    *,
    client: Client | None = None,
) -> dict[str, dict[str, int]]:
    """Push anomalies to LangSmith datasets, scoped by run_type.

    Scoping:
    - known project tools → ``{flow}_{tool_name}``
    - generic ``model``/``tools`` wrapper spans → ``{context_name}_{error_kind}``
      (for example ``TodoListMiddleware_after_model_hard_error``)
    - other flowless spans → ``{run_name}_{error_kind}``

    Appends to existing datasets and skips examples whose run_id has already
    been pushed (idempotent — safe to call repeatedly on the same report).

    Anomaly categories and detailed signal strings are written into example
    metadata. Baseline thresholds are not copied; the report is already a
    snapshot of the comparison that triggered each anomaly.

    Args:
        report: From ``detect_anomalies_async()``.
        client: Optional pre-constructed LangSmith Client. Defaults to a new
                Client() so the caller can inject a mock in tests.

    Returns:
        Dict keyed by dataset name, each value ``{"new": int, "total": int}``.
    """
    ls = client or Client()

    # Group by dataset name so we can batch-create examples per dataset.
    groups_by_dataset: dict[str, list[tuple[str, FailedSpan]]] = defaultdict(list)
    for anomaly in report.anomalies:
        for span in anomaly.failed_spans:
            groups_by_dataset[_dataset_name(span)].append((anomaly.trace_id, span))

    created: dict[str, dict[str, int]] = {}
    for dataset_name, items in groups_by_dataset.items():
        first_span = items[0][1]
        scope = _SCOPE_BY_RUN_TYPE.get(first_span.run_type or "", "tool")
        description = f"{_dataset_description(first_span)} Scope={scope}."

        if ls.has_dataset(dataset_name=dataset_name):
            dataset = ls.read_dataset(dataset_name=dataset_name)
        else:
            dataset = ls.create_dataset(
                dataset_name=dataset_name,
                description=description,
                metadata={
                    "run_type":     first_span.run_type,
                    "run_name":     first_span.run_name,
                    "context_name": first_span.context_name,
                },
            )

        existing_run_ids: set[str] = set()
        for ex in ls.list_examples(dataset_id=dataset.id):
            rid = (ex.metadata or {}).get("run_id")
            if rid:
                existing_run_ids.add(rid)

        new_examples = []
        for trace_id, span in items:
            if span.id in existing_run_ids:
                continue

            new_examples.append({
                "inputs": _normalize_example_inputs(span.inputs),
                "outputs": span.outputs,
                "metadata": {
                    "run_id": span.id,
                    "trace_id": trace_id,
                    "run_name": span.run_name,
                    "flow": span.flow,
                    "run_type": span.run_type,
                    "context_name": span.context_name,
                    "errors": span.errors,
                    "signals": span.signals,
                    "pass_criteria": f"Must handle gracefully: {'; '.join(span.signals)}",
                },
            })

        if new_examples:
            ls.create_examples(dataset_id=dataset.id, examples=new_examples)

        created[dataset_name] = {
            "new": len(new_examples),
            "total": len(existing_run_ids) + len(new_examples),
        }

    return created
