"""
Weekly golden dataset evaluation.

Runs aevaluate against one of the three golden datasets and gates on pass rate.
Target functions auto-approve HITL interrupts so eval never blocks for input.

Usage:
    uv run --env-file .env python eval/run_weekly_eval.py --dataset ingest
    uv run --env-file .env python eval/run_weekly_eval.py --dataset query
    uv run --env-file .env python eval/run_weekly_eval.py --dataset marp
    uv run --env-file .env python eval/run_weekly_eval.py --dataset ingest --no-gate

Exit codes:
    0 — pass (or --no-gate)
    1 — pass rate below threshold
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from langchain_core.utils.uuid import uuid7
from langgraph.types import Command
from langsmith.evaluation import aevaluate

from src.agents.agent import create_supervisor
from eval.golden_evaluators import (
    no_crash, trajectory_subsequence,
    index_updated, log_updated, min_page_count, has_wikilinks,
    graph_updated, wiki_faithfulness, no_hallucination,
    correctly_refused, correctly_handled_duplicate,
    query_is_read_only, answer_grounded, answer_correctness,
    query_correctly_handled,
    has_marp_frontmatter, has_lead_slide, has_content_slides,
    css_embedded, file_saved, used_web_search, slide_quality,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Target function helpers
# ---------------------------------------------------------------------------

def _auto_approve(interrupts: list) -> list:
    """Build an auto-approve decisions list for HITL interrupts."""
    action_requests = interrupts[0].value["action_requests"]
    return [{"type": "approve"} for _ in action_requests]


async def _stream_agent(message: str, config: dict) -> tuple[list[str], list[str], str]:
    """Run agent with HITL auto-approve, return (trajectory, files_written, final_message)."""
    agent = await create_supervisor()
    payload: dict | Command = {
        "messages": [{"role": "user", "content": message}]
    }
    trajectory: list[str] = []
    files_written: list[str] = []
    final_message = ""

    while True:
        pending = None

        async for chunk in agent.astream(
            payload,
            config=config,
            version="v2",
            subgraphs=True,
            stream_mode=["values", "updates"],
        ):
            if chunk["type"] == "values" and chunk.get("interrupts"):
                pending = chunk["interrupts"]
                break

            if chunk["type"] == "updates":
                for node_data in chunk["data"].values():
                    if not isinstance(node_data, dict):
                        continue
                    for msg in node_data.get("messages", []):
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                trajectory.append(tc["name"])
                                if tc["name"] in ("write_file", "edit_file"):
                                    path = (tc.get("args") or {}).get("path", "")
                                    if path:
                                        files_written.append(path)

        if not pending:
            break
        payload = Command(resume={"decisions": _auto_approve(pending)})

    final_state = await agent.aget_state(config)
    messages = (final_state.values or {}).get("messages", [])
    if messages:
        last = messages[-1]
        content = getattr(last, "content", "")
        final_message = content if isinstance(content, str) else str(content)

    return trajectory, files_written, final_message


def _read_wiki_pages(files_written: list[str]) -> str:
    pages = [
        p for p in files_written
        if "wiki/" in p and not any(x in p for x in ("index.md", "log.md", "graph.json", "citations.json"))
    ]
    content = ""
    for p in pages:
        full = REPO_ROOT / p.lstrip("/")
        if full.exists():
            content += full.read_text() + "\n\n"
    return content


# ---------------------------------------------------------------------------
# Target functions
# ---------------------------------------------------------------------------

async def run_ingest(inputs: dict) -> dict:
    config = {"configurable": {"thread_id": str(uuid7())}}
    trajectory, files_written, final_message = await _stream_agent(inputs["message"], config)
    wiki_content = _read_wiki_pages(files_written)
    return {
        "trajectory": trajectory,
        "files_written": files_written,
        "final_message": final_message,
        "wiki_content": wiki_content,
    }


async def run_query(inputs: dict) -> dict:
    config = {"configurable": {"thread_id": str(uuid7())}}
    trajectory, _, final_message = await _stream_agent(inputs["message"], config)
    return {"trajectory": trajectory, "final_message": final_message}


async def run_marp(inputs: dict) -> dict:
    config = {"configurable": {"thread_id": str(uuid7())}}
    trajectory, files_written, final_message = await _stream_agent(inputs["message"], config)

    slide_path = next(
        (p for p in files_written if p.endswith(".md") and "marp" in p.lower()),
        "",
    )
    slide_content = ""
    if slide_path:
        full = REPO_ROOT / slide_path.lstrip("/")
        if full.exists():
            slide_content = full.read_text()

    return {
        "trajectory": trajectory,
        "slide_path": slide_path,
        "slide_content": slide_content,
        "final_message": final_message,
    }


# ---------------------------------------------------------------------------
# Dataset config
# ---------------------------------------------------------------------------

DATASETS: dict[str, dict] = {
    "ingest": {
        "dataset": "paper2wiki-golden-ingest",
        "target": run_ingest,
        "evaluators": [
            no_crash, index_updated, log_updated, min_page_count,
            has_wikilinks, graph_updated, trajectory_subsequence,
            wiki_faithfulness, no_hallucination,
            correctly_refused, correctly_handled_duplicate,
        ],
        "hard_gate_keys": {"no_crash", "index_updated", "log_updated"},
        "threshold": 0.8,
        "num_repetitions": 3,
    },
    "query": {
        "dataset": "paper2wiki-golden-query",
        "target": run_query,
        "evaluators": [
            no_crash, query_is_read_only, trajectory_subsequence,
            answer_grounded, answer_correctness, query_correctly_handled,
        ],
        "hard_gate_keys": {"no_crash", "query_is_read_only"},
        "threshold": 0.75,
        "num_repetitions": 1,
    },
    "marp": {
        "dataset": "paper2wiki-golden-marp",
        "target": run_marp,
        "evaluators": [
            has_marp_frontmatter, has_lead_slide, has_content_slides,
            css_embedded, file_saved, used_web_search, slide_quality,
        ],
        "hard_gate_keys": {"has_marp_frontmatter", "file_saved"},
        "threshold": 0.67,
        "num_repetitions": 2,
    },
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def _run(dataset_key: str, no_gate: bool) -> int:
    cfg = DATASETS[dataset_key]
    print(f"Evaluating: {cfg['dataset']}")

    results = await aevaluate(
        cfg["target"],
        data=cfg["dataset"],
        evaluators=cfg["evaluators"],
        experiment_prefix=f"golden-{dataset_key}",
        num_repetitions=cfg["num_repetitions"],
    )
    print(f"Results: {results.url}")

    if no_gate:
        print("--no-gate: tracking only, not gating on pass rate")
        return 0

    hard_keys = cfg["hard_gate_keys"]
    total = 0
    passed = 0
    async for example_result in results:
        total += 1
        scores = [
            ev.score
            for ev in example_result.evaluation_results.results
            if ev.key in hard_keys and ev.score is not None
        ]
        if not scores or (sum(scores) / len(scores)) >= 0.5:
            passed += 1

    pass_rate = passed / total if total else 0.0
    threshold = cfg["threshold"]
    print(f"Pass rate: {passed}/{total} = {pass_rate:.0%} (threshold {threshold:.0%})")

    if pass_rate < threshold:
        print("FAIL")
        return 1
    print("PASS")
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", choices=["ingest", "query", "marp"], required=True)
    p.add_argument("--no-gate", action="store_true",
                   help="Track results without blocking on pass rate (calibration mode)")
    args = p.parse_args()

    if args.dataset == "marp" and not os.getenv("DAYTONA_API_KEY"):
        print("DAYTONA_API_KEY not set — skipping marp eval (tracking only)")
        sys.exit(0)

    sys.exit(asyncio.run(_run(args.dataset, args.no_gate)))


if __name__ == "__main__":
    main()
