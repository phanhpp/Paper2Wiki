"""LangSmith dataset creation from anomaly reports.

Called after ``detect_anomalies_async()``:

    create_datasets_from_anomaly_report(report)
        → datasets scoped by flow / run name / middleware context
        → appends new examples, skips already-pushed run_ids
"""

from __future__ import annotations

import ast
import asyncio
import json
import re
from collections import defaultdict
from typing import Any

from langsmith import AsyncClient

from src.tools.observability_eval_tools.anomaly_detection import AnomalyReport, AnomalySignal, FailedSpan
from langchain_core.tools import tool

_TOOL_EVAL_CATEGORY: dict[str, str] = {
    "fetch_arxiv": "retrieval",
    "parse_pdf_docling": "retrieval",
    "web_search": "retrieval",
    "web_extract": "retrieval",
    "quick_wiki_integrity_check": "health",
}

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


def _generate_PR_cases(report: AnomalyReport) -> list[dict[str, Any]]:
    """Generate candidate eval/cases.json entries for tool hard errors only.

    Only hard_error spans with run_type == "tool" produce cases — these are
    deterministic failures: fix the crash, add a regression case, it must never
    crash again on those inputs.

    Latency/token/step spikes are performance anomalies and go to LangSmith
    datasets only — they cannot be reproduced deterministically in run_gate.py.
    """
    cases: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for anomaly in report.anomalies:
        for span in anomaly.failed_spans:
            if span.run_type != "tool":
                continue
            if "hard_error" not in span.errors:
                continue
            if span.id in seen_ids:
                continue
            seen_ids.add(span.id)

            cases.append({
                "id": f"regression_{span.run_name}_{span.id[:8]}",
                "type": "regression",
                "category": _TOOL_EVAL_CATEGORY.get(span.run_name, "boundary"),
                "tool": span.run_name,
                "inputs": _normalize_example_inputs(span.inputs),
                # No expect_* set — reviewer must fill in after fixing the crash:
                # - valid input that crashed → add expect_keys once tool works
                # - invalid input → add expect_error: true (graceful handling)
                "_review": "fill in expect_keys or expect_error after fix is merged",
            })

    return cases


@tool()
async def create_datasets_from_anomaly_report(
    report: AnomalyReport,
    eval_cases: bool = True,
) -> dict[str, Any]:
    """Push anomalies to LangSmith datasets, scoped by run_type.

    Appends to existing datasets and skips examples whose run_id has already
    been pushed (idempotent — safe to call repeatedly on the same report).

    Args:
        report: From ``detect_anomalies_async()``.
        eval_cases: If True, also generate candidate ``eval/cases.json`` entries
            for tool-level anomalies. Returned under ``suggested_cases`` — the
            skill writes them to ``eval/cases.json`` after HITL approval.

    Returns:
        ``{"datasets": {name: {"new": int, "total": int}}, "suggested_cases": [...]}``
    """
    ls = AsyncClient()

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

        # AsyncClient has no has_dataset — attempt read, create on miss.
        try:
            dataset = await ls.read_dataset(dataset_name=dataset_name)
        except Exception:
            dataset = await ls.create_dataset(
                dataset_name=dataset_name,
                description=description,
                metadata={
                    "run_type":     first_span.run_type,
                    "run_name":     first_span.run_name,
                    "context_name": first_span.context_name,
                },
            )

        existing_run_ids: set[str] = set()
        async for ex in ls.list_examples(dataset_id=dataset.id):
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

        # AsyncClient has no create_examples (plural) — create concurrently.
        if new_examples:
            await asyncio.gather(*[
                ls.create_example(
                    inputs=ex["inputs"],
                    outputs=ex["outputs"],
                    metadata=ex["metadata"],
                    dataset_id=dataset.id,
                )
                for ex in new_examples
            ])

        created[dataset_name] = {
            "new": len(new_examples),
            "total": len(existing_run_ids) + len(new_examples),
        }

    result: dict[str, Any] = {"datasets": created}
    if eval_cases:
        result["suggested_cases"] = _generate_PR_cases(report)
    return result
