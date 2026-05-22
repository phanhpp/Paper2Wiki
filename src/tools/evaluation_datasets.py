"""LangSmith dataset creation from anomaly reports.

Called after ``detect_anomalies_async()``:

    create_datasets_from_anomaly_report(report)
        → datasets scoped by flow / run name / middleware context
        → appends new examples, skips already-pushed run_ids
"""

from __future__ import annotations

import re
from collections import defaultdict

from langsmith import Client

from src.tools.anomaly_detection import AnomalyError, AnomalyReport, FailedSpan

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


def _error_suffix(errors: list[AnomalyError]) -> str:
    """Return a suffix from typed error categories only."""
    kinds: list[AnomalyError] = []
    seen: set[AnomalyError] = set()
    for error in errors:
        if error not in seen:
            seen.add(error)
            kinds.append(error)
    return "_".join(kinds) or "anomaly"


def _dataset_name(span: FailedSpan) -> str:
    """Derive a dataset name from a span's run_type and flow.

    Scoping rules:
    - known tool → ``{flow}_{run_name}`` (specific project tool that failed)
    - generic wrapper names ``model``/``tools`` → ``{context_name}_{error_kind}``
      when parser context is available
    - other flowless spans → ``{run_name}_{error_kind}``
    """
    run_name = _safe_dataset_component(span.run_name)
    suffix = _error_suffix(span.errors)

    if span.flow:
        return f"{_safe_dataset_component(span.flow)}_{run_name}"

    if span.run_name in _GENERIC_WRAPPER_NAMES:
        source_name = _safe_dataset_component(span.context_name or span.run_name)
    else:
        source_name = run_name
    return f"{source_name}_{suffix}"


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

    groups: dict[str, list[tuple[str, FailedSpan]]] = defaultdict(list)
    for anomaly in report.anomalies:
        for span in anomaly.failed_spans:
            groups[_dataset_name(span)].append((anomaly.trace_id, span))

    created: dict[str, dict[str, int]] = {}
    for dataset_name, items in groups.items():
        first_span = items[0][1]
        scope = _SCOPE_BY_RUN_TYPE.get(first_span.run_type or "", "tool")
        description = f"{scope.title()}-level failures: {first_span.flow or 'unknown'} flow"

        if ls.has_dataset(dataset_name=dataset_name):
            dataset = ls.read_dataset(dataset_name=dataset_name)
        else:
            dataset = ls.create_dataset(
                dataset_name=dataset_name,
                description=description,
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
                "inputs": span.inputs,
                "outputs": span.outputs,
                "metadata": {
                    "run_id": span.id,
                    "trace_id": trace_id,
                    "run_name": span.run_name,
                    "flow": span.flow,
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
