"""
Weekly golden dataset evaluation.

Runs aevaluate against one of the golden datasets and gates on pass rate.
Target functions handle HITL interrupts automatically so eval never blocks for
interactive approval.

HITL auto-approval policy
-------------------------

For ingest/query evals (``policy_name="wiki"``):
    Approved:
        - ``write_file`` / ``edit_file`` only when the target path is under ``wiki/``
        - ``execute`` only for read-only shell introspection commands such as
          ``ls``, ``rg``, ``grep``, ``cat``, ``head``, ``tail``, ``find``, ``wc``,
          ``sort``, or other commands mentioning allowed read roots
        - read/search access to ``/large_tool_results/`` for offloaded tool outputs
    Rejected:
        - writes outside ``wiki/``
        - destructive/write-capable shell commands such as ``rm``, ``mv``, ``cp``,
          ``git commit/push/reset/checkout/rebase/merge``, ``chmod``, ``chown``,
          ``curl``, ``wget``, ``scp``, ``ssh``, ``tee``, ``sed -i``,
          package installs, ``sudo``, ``kill``/``pkill``, and shell redirection
        - any other interrupted tool action not explicitly approved

For marp evals (``policy_name="marp"``):
    Approved writes are limited to ``marp-slides/``. Read roots include
    ``wiki/``, ``skills/``, ``memories/``, ``marp-slides/``, and
    ``/large_tool_results/``.

Every decision is printed as ``HITL APPROVED`` or ``HITL REJECTED`` with the
policy name, action args, and reason.

Usage:
    uv run --env-file .env python eval/run_weekly_eval.py --dataset ingest
    uv run --env-file .env python eval/run_weekly_eval.py --dataset query
    uv run --env-file .env python eval/run_weekly_eval.py --dataset marp
    uv run --env-file .env python eval/run_weekly_eval.py --dataset ingest --no-gate
    uv run --env-file .env python eval/run_weekly_eval.py --dataset ingest --use-cached-full-ingest
    uv run --env-file .env python eval/run_weekly_eval.py --dataset query --use-cached-attention-query --filter-id 
    uv run --env-file .env python eval/run_weekly_eval.py --dataset marp --use-cached-pug-marp --use-cached-transformer-business-theme-marp --no-gate --filter-id transformer-business-theme
    uv run --env-file .env python eval/run_weekly_eval.py --dataset marp --use-cached-pug-marp --no-gate --filter-id pugs-colorful-two-slide

Exit codes:
    0 — pass (or --no-gate)
    1 — pass rate below threshold
"""

from __future__ import annotations

import argparse
import asyncio
import ast
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import inspect
from functools import wraps

from langchain_core.utils.uuid import uuid7
from langgraph.types import Command
from langsmith.evaluation import aevaluate
from langsmith.schemas import Run, Example

from src.agents.agent import create_supervisor
from eval.eval_utils import message_text
from eval.golden_evaluators import (
    no_crash, trajectory_subsequence, ingest_outcome_correct, min_page_count, has_wikilinks,
    maintenance_files_updated, wiki_faithfulness, no_hallucination,
    answer_quality,
    has_marp_frontmatter, has_lead_slide, has_content_slides,
    css_embedded, file_saved, used_web_search, slide_quality,
)

REPO_ROOT = Path(__file__).resolve().parents[1]  # repo root — paths from agent are relative to this
CACHED_PARTIAL_INGEST_OUTPUT = REPO_ROOT / "eval/cached_outputs/paper2web_partial_ingest.json"
CACHED_FULL_INGEST_OUTPUT = REPO_ROOT / "eval/cached_outputs/graphrag_full_ingest.json"
CACHED_ATTENTION_CONTRIBUTION_QUERY_OUTPUT = REPO_ROOT / "eval/cached_outputs/attention_contribution_query.json"
CACHED_TRANSFORMER_ARCHITECTURE_QUERY_OUTPUT = REPO_ROOT / "eval/cached_outputs/transformer_architecture_query.json"
CACHED_PUG_MARP_OUTPUT = REPO_ROOT / "eval/cached_outputs/pug_marp.json"
CACHED_TRANSFORMER_BUSINESS_THEME_MARP_OUTPUT = REPO_ROOT / "eval/cached_outputs/transformer_marp.json"

# ---------------------------------------------------------------------------
# Target function helpers
# ---------------------------------------------------------------------------

# HITL write allowlists per eval dataset (paths may be repo-relative or sandbox absolute).
_HITL_POLICIES: dict[str, dict[str, tuple[str, ...]]] = {
    "wiki": {
        "write_prefixes": ("wiki/",),
        "read_prefixes": ("wiki/", "skills/", "memories/", "large_tool_results/"),
    },
    "marp": {
        "write_prefixes": ("marp-slides/",),
        "read_prefixes": ("wiki/", "skills/", "memories/", "marp-slides/", "large_tool_results/"),
    },
}

# Shell patterns that must never run during eval (even if HITL would approve).
_EXEC_DENY_RE = re.compile(
    r"\b("
    r"rm|rmdir|mv\b|cp\b|git\s+(commit|push|reset|checkout|rebase|merge)|"
    r"chmod|chown|curl|wget|scp|ssh\b|tee\b|sed\s+-i|"
    r"pip\s+install|npm\s+install|uv\s+add|python\s+-c|sudo|kill|pkill"
    r")\b|>>?[^\s&]",
    re.IGNORECASE,
)

_READ_ONLY_CMD_RE = re.compile(
    r"^\s*(grep|rg|cat|head|tail|ls|find|wc|sort|test)\b",
    re.IGNORECASE,
)


def _normalize_path(path: str) -> str:
    return (path or "").replace("\\", "/").lower()


def _path_allowed_write(path: str, write_prefixes: tuple[str, ...]) -> bool:
    """True if path is under an allowed write root (repo-relative or nested sandbox path)."""
    p = _normalize_path(path)
    for prefix in write_prefixes:
        root = prefix.lower().rstrip("/")
        if p.startswith(root + "/") or p == root or f"/{root}/" in p:
            return True
    return False


def _action_path(args: dict) -> str:
    """Return the file path from DeepAgents action args (`file_path`) or legacy `path`."""
    return args.get("file_path") or args.get("path") or ""


def _tool_args_preview(tool_name: str, args: dict) -> str:
    """Return compact tool args for trajectory logging, preserving args needed by evaluators."""
    text = str(args or {})
    if tool_name == "save_output":
        return text
    return text[:80]


def _execute_allowed(command: str, read_prefixes: tuple[str, ...]) -> bool:
    """Allow read-only shell introspection; deny destructive or write-via-shell commands."""
    if not command or not isinstance(command, str):
        return False
    if _EXEC_DENY_RE.search(command):
        return False
    cmd = command.strip()
    if _READ_ONLY_CMD_RE.match(cmd):
        return True
    cmd_lower = cmd.lower()
    return any(prefix.lower() in cmd_lower for prefix in read_prefixes)


def _hitl_decision(action: dict, policy: dict[str, tuple[str, ...]]) -> tuple[dict, str]:
    """Map one HITL action_request to approve/reject with a debug reason."""
    name = action["name"]
    args = action.get("args") or {}
    write_prefixes = policy["write_prefixes"]
    read_prefixes = policy["read_prefixes"]

    if name in ("write_file", "edit_file"):
        path = _action_path(args)
        if not _path_allowed_write(path, write_prefixes):
            return {"type": "reject"}, f"path {path!r} outside allowed write roots: {write_prefixes}"
        return {"type": "approve"}, f"path {path!r} allowed under write roots: {write_prefixes}"

    if name == "execute":
        cmd = args.get("command", "")
        if _execute_allowed(cmd, read_prefixes):
            return {"type": "approve"}, "read-only command allowed by eval policy"
        return {"type": "reject"}, "command denied by eval policy"

    return {"type": "reject"}, "tool is not auto-approved in eval"


def _auto_approve(interrupts: list, *, policy_name: str = "wiki") -> list[dict]:
    """Build HITL resume decisions for eval (no interactive prompt).

    policy_name "wiki" (ingest/query): write/edit only under wiki/.
    policy_name "marp": write/edit only under marp-slides/ (incl. sandbox paths).
    execute: read-only commands + paths under policy read_prefixes; else reject.
    """
    policy = _HITL_POLICIES[policy_name]
    interrupt_value = interrupts[0].value
    action_requests = interrupt_value["action_requests"]

    decisions = []
    for action in action_requests:
        decision, reason = _hitl_decision(action, policy)
        status = "APPROVED" if decision["type"] == "approve" else "REJECTED"
        print(
            f"\n🔔 HITL {status} [{policy_name}] {action['name']}: "
            f"{action.get('args', {})} — {reason}",
            flush=True,
        )
        decisions.append(decision)
    return decisions

async def _stream_agent(
    message: str,
    config: dict,
    *,
    eval_mode: bool = True,
) -> tuple[list[str], list[str], str]:
    f"""Run the supervisor to completion with HITL auto-approved.

    Streams until no interrupts remain, collecting tool names in call order and
    paths touched by write_file / edit_file. Returns the final assistant message text.

    eval_mode=True (ingest/query): guarded backend (reads skills/wiki/memories only),
        no marp subagent. Same agent as production except path guards + HITL auto-approve.
    eval_mode=False (marp): includes marp-slide-creator + Daytona; needs DAYTONA_API_KEY.
    
    Returns: 
        - trajectory: list of dicts with tool name and args
        - files_written: list of paths written
        - final_message: final message from the agent
    """
    print("===Stream agent running===")
    thread_id = (config.get("configurable") or {}).get("thread_id")
    if not thread_id:
        thread_id = f"eval-{uuid7()}"
        config.setdefault("configurable", {})["thread_id"] = thread_id

    agent = await create_supervisor(thread_id, eval_mode=eval_mode)
    payload: dict | Command = {
        "messages": [{"role": "user", "content": message}]
    }
    trajectory: list[dict] = []
    files_written: list[str] = []
    final_message = ""

    while True:
        interrupt_object = None

        async for chunk in agent.astream(
            payload,
            config=config,
            version="v2",
            subgraphs=True,
            stream_mode=["values", "updates", "messages"],
        ):
            if chunk["type"] == "values" and chunk.get("interrupts"):
                interrupt_object = chunk["interrupts"]
                break

            if chunk["type"] == "updates":
                if chunk["data"].get("__interrupt__"):
                    interrupt_object = chunk["data"]["__interrupt__"]
                    break
                for node_data in chunk["data"].values():
                    if not isinstance(node_data, dict):
                        continue
                    messages = node_data.get("messages", [])
                    # deepagents wraps message lists in an Overwrite sentinel —
                    # extract the underlying list the same way stream.py does.
                    if not isinstance(messages, list):
                        messages = getattr(messages, "value", None) or []
                        if not isinstance(messages, list):
                            continue
                    for msg in messages:
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                args_preview = _tool_args_preview(tc["name"], tc.get("args") or {})
                                print(f"\n🔧 {tc['name']}({args_preview})", flush=True)
                                trajectory.append({"name": tc["name"], "args": args_preview})
                                if tc["name"] in ("write_file", "edit_file"):
                                    path = _action_path(tc.get("args") or {})
                                    if path:
                                        files_written.append(path)
            
            elif chunk["type"] == "messages":
                msg, _ = chunk["data"]

                if not msg.content:
                    continue

                if isinstance(msg.content, str):
                    print(msg.content, end="", flush=True)
                elif isinstance(msg.content, list):  # Message from AI by default is a list
                    for block in msg.content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                print(text, end="", flush=True)

        if not interrupt_object:
            break
        hitl_policy = "wiki" if eval_mode else "marp"
        payload = Command(
            resume={"decisions": _auto_approve(interrupt_object, policy_name=hitl_policy)}
        )

    final_state = await agent.aget_state(config)
    messages = (final_state.values or {}).get("messages", [])
    if messages:
        last = messages[-1]
        final_message = message_text(getattr(last, "content", ""))

    # Debugging
    print(f"\nTrajectory: {trajectory}")
    print(f"Files written: {files_written}")
    print(f"Final message: {final_message}")
    return trajectory, files_written, final_message


def _read_wiki_pages(files_written: list[str]) -> str:
    """Concatenate body text from wiki pages written during an ingest run.

    Skips index.md, log.md, graph.json, and citations.json — evaluators judge
    article content, not bookkeeping files.
    """
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


def _extract_marp_slide_path(trajectory: list[dict], final_message: str) -> str:
    """Return the host-relative Marp output path saved by the Daytona subagent."""
    for step in trajectory:
        if step.get("name") != "save_output":
            continue
        try:
            args = ast.literal_eval(step.get("args", ""))
        except (SyntaxError, ValueError):
            args = {}
        host_path = args.get("host_relative_path", "")
        if host_path.endswith(".md") and host_path.startswith("marp-slides/"):
            return host_path

    match = re.search(r"`?(/?marp-slides/[^`\s]+\.md)`?", final_message)
    if match:
        return match.group(1).lstrip("/")
    return ""


# ---------------------------------------------------------------------------
# Target functions
# ---------------------------------------------------------------------------

async def run_ingest(inputs: dict) -> dict:
    """LangSmith target for any2wiki-golden-ingest.

    Expects inputs["message"]. Returns trajectory, files_written, final_message,
    and wiki_content for ingest evaluators (faithfulness, page count, etc.).
    """
    if (
        os.environ.get("ANY2WIKI_USE_CACHED_PARTIAL_INGEST") == "1"
        and "paper2web" in inputs.get("message", "").lower()
        and "partially ingested" in inputs.get("message", "").lower()
    ):
        print(f"Using cached partial-ingest output: {CACHED_PARTIAL_INGEST_OUTPUT}")
        return json.loads(CACHED_PARTIAL_INGEST_OUTPUT.read_text())
    if (
        os.environ.get("ANY2WIKI_USE_CACHED_FULL_INGEST") == "1"
        and "graphrag" in inputs.get("message", "").lower()
        and "query-focused summarization" in inputs.get("message", "").lower()
    ):
        print(f"Using cached full-ingest output: {CACHED_FULL_INGEST_OUTPUT}")
        return json.loads(CACHED_FULL_INGEST_OUTPUT.read_text())

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
    """LangSmith target for any2wiki-golden-query.

    Expects inputs["message"]. Read-only queries should not write wiki files;
    returns trajectory and final_message for grounding / correctness judges.
    """
    msg = inputs.get("message", "").lower()
    if (
        os.environ.get("ANY2WIKI_USE_CACHED_ATTENTION_CONTRIBUTION_QUERY") == "1"
        and "key contributions of the attention" in msg
    ):
        print(f"Using cached attention contribution query output: {CACHED_ATTENTION_CONTRIBUTION_QUERY_OUTPUT}")
        return json.loads(CACHED_ATTENTION_CONTRIBUTION_QUERY_OUTPUT.read_text())
    if (
        os.environ.get("ANY2WIKI_USE_CACHED_TRANSFORMER_ARCHITECTURE_QUERY") == "1"
        and "architecture of the transformer" in msg
    ):
        print(f"Using cached transformer architecture query output: {CACHED_TRANSFORMER_ARCHITECTURE_QUERY_OUTPUT}")
        return json.loads(CACHED_TRANSFORMER_ARCHITECTURE_QUERY_OUTPUT.read_text())

    config = {"configurable": {"thread_id": str(uuid7())}}
    trajectory, _, final_message = await _stream_agent(inputs["message"], config)
    return {"trajectory": trajectory, "final_message": final_message}


async def run_marp(inputs: dict) -> dict:
    """LangSmith target for any2wiki-golden-marp.

    Expects inputs["message"]. Routes through the marp-slide-creator subagent
    (Daytona sandbox). Returns slide_path, slide_content, and trajectory.
    """
    msg = inputs.get("message", "").lower()
    if (
        os.environ.get("ANY2WIKI_USE_CACHED_PUG_MARP") == "1"
        and "cute pug" in msg
    ):
        print(f"Using cached pug marp output: {CACHED_PUG_MARP_OUTPUT}")
        return json.loads(CACHED_PUG_MARP_OUTPUT.read_text())
    
    if (
        os.environ.get("ANY2WIKI_USE_CACHED_TRANSFORMER_BUSINESS_THEME_MARP") == "1"
        and "transformer architecture" in msg
    ):
        print(f"Using cached transformer business theme marp output: {CACHED_TRANSFORMER_BUSINESS_THEME_MARP_OUTPUT}")
        return json.loads(CACHED_TRANSFORMER_BUSINESS_THEME_MARP_OUTPUT.read_text())
    
    config = {"configurable": {"thread_id": str(uuid7())}}
    trajectory, files_written, final_message = await _stream_agent(
        inputs["message"], config, eval_mode=False,
    )

    slide_path = _extract_marp_slide_path(trajectory, final_message)
    slide_content = ""
    if slide_path:
        full = REPO_ROOT / slide_path.lstrip("/")
        if full.exists():
            slide_content = full.read_text()

    return {
        "trajectory": trajectory,
        "files_written": files_written,
        "slide_path": slide_path,
        "slide_content": slide_content,
        "final_message": final_message,
    }



# ---------------------------------------------------------------------------
# Per-case evaluator gating
# ---------------------------------------------------------------------------

def _gate(fn):
    """Wrap an evaluator with per-case gating based on example.metadata["evaluators"].

    Every evaluator in DATASETS should be wrapped with _gate() so that per-case
    evaluator lists defined in the golden dataset JSON are respected:

        metadata["evaluators"]: ["no_crash", "trajectory_subsequence", "answer_quality"]

    Behaviour:
    - If metadata["evaluators"] is absent: evaluator always runs (backwards-compatible).
    - If metadata["evaluators"] is present and fn.__name__ IS listed: evaluator runs.
    - If metadata["evaluators"] is present and fn.__name__ is NOT listed: evaluator is
      skipped and returns score=None so LangSmith records the key as N/A, not a failure.

    This allows the same evaluator pool to serve all cases in a dataset while individual
    cases opt in/out of specific evaluators (e.g. negative query cases skip answer_quality;
    already-ingested ingest cases skip min_page_count).
    """
    # Do NOT use @wraps here — it sets __wrapped__ which causes inspect.signature
    # to follow the chain to the original fn signature. LangSmith would then see
    # e.g. (outputs) and call wrapper(outputs_value), binding it to `run` and
    # raising "missing 1 required positional argument: 'example'".
    def wrapper(run, example) -> dict:
        allowed = (example.metadata or {}).get("evaluators")
        if allowed is not None and fn.__name__ not in allowed:
            return {"key": fn.__name__, "score": None, "comment": "n/a for this case type"}

        params = list(inspect.signature(fn).parameters.keys())
        outputs = run.outputs or {}
        inputs = run.inputs or {}
        ref_outputs = example.outputs or {}

        if params[:2] == ["run", "example"]:
            return fn(run, example)
        elif params == ["outputs"]:
            return fn(outputs)
        elif params[:2] == ["inputs", "outputs"] and len(params) == 2:
            return fn(inputs, outputs)
        elif params[:3] == ["inputs", "outputs", "reference_outputs"]:
            return fn(inputs, outputs, ref_outputs)
        else:
            return fn(run, example)

    wrapper.__name__ = fn.__name__  # for LangSmith key naming only, not signature inspection
    return wrapper


_ALL_INGEST_EVALUATORS = [
    no_crash, trajectory_subsequence,
    min_page_count,
    has_wikilinks, maintenance_files_updated,
    wiki_faithfulness, no_hallucination, ingest_outcome_correct,
]

# ---------------------------------------------------------------------------
# Dataset config — keys: dataset, target, evaluators, hard_gate_keys,
# threshold, num_repetitions. hard_gate_keys are evaluation result keys, not
# necessarily evaluator function names (e.g. trajectory_subsequence emits
# trajectory_no_forbidden).
# ---------------------------------------------------------------------------

DATASETS: dict[str, dict] = {
    "ingest": {
        "dataset": "any2wiki-golden-ingest",
        "target": run_ingest,
        "evaluators": [_gate(fn) for fn in _ALL_INGEST_EVALUATORS],
        "hard_gate_keys": {"no_crash"},
        "threshold": 0.8,
        "num_repetitions": 1,
    },
    "query": {
        "dataset": "any2wiki-golden-query",
        "target": run_query,
        "evaluators": [_gate(fn) for fn in [
            no_crash, trajectory_subsequence,
            answer_quality
        ]],
        "hard_gate_keys": {"no_crash", "trajectory_no_forbidden"},
        "threshold": 0.75,
        "num_repetitions": 1,
    },
    "marp": {
        "dataset": "any2wiki-golden-marp",
        "target": run_marp,
        "evaluators": [_gate(fn) for fn in [
            trajectory_subsequence, has_marp_frontmatter, has_lead_slide, has_content_slides,
            css_embedded, file_saved, used_web_search, slide_quality,
        ]],
        "hard_gate_keys": {"has_marp_frontmatter", "file_saved"},
        "threshold": 0.67,
        "num_repetitions": 1,
    },
}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

async def _run(dataset_key: str, no_gate: bool, filter_metadata: dict | None = None) -> int:
    """Run aevaluate for one golden dataset and optionally gate on pass rate.

    filter_metadata filters which examples run, matched against each example's metadata dict.
    Only metadata fields work here (type, arxiv_id, etc.) — inputs fields like message do not.

    Example:
        filter_metadata={"type": "already-ingested"}   # run one case by type
        filter_metadata={"arxiv_id": "2404.16130"}     # run one case by paper

    An example passes the hard gate when the mean score of hard_gate_keys evaluators
    is >= 0.5. Overall pass rate must meet the dataset threshold unless no_gate is set.

    Returns 0 on pass (or calibration mode), 1 when pass rate is below threshold.
    """
    from langsmith import Client as LangSmithClient
    client = LangSmithClient()
    cfg = DATASETS[dataset_key]
    dataset_name = cfg["dataset"]
    print(f"Evaluating: {dataset_name}" + (f" (filter: {filter_metadata})" if filter_metadata else ""))

    # filter_metadata goes to list_examples (selects which examples to run),
    # not to aevaluate's metadata param (which tags the experiment).
    # Materialize to list — list_examples returns a sync generator which aevaluate
    # (async) cannot iterate; passing a concrete list avoids StopAsyncIteration.
    if filter_metadata:
        examples = list(client.list_examples(dataset_name=dataset_name, metadata=filter_metadata))
        print(f"Filter {filter_metadata} matched {len(examples)} example(s)")
        if not examples:
            print("No examples matched — check that metadata fields exist on the pushed examples")
            return 0
        data = examples
    else:
        data = dataset_name

    results = await aevaluate(
        cfg["target"],
        data=data,
        evaluators=cfg["evaluators"],
        experiment_prefix=f"golden-{dataset_key}",
        num_repetitions=cfg["num_repetitions"],
        client=client,
    )
    print(f"Results: {results.url}")

    
    if no_gate:
        print("--no-gate: tracking only, not gating on pass rate")
        aggregated_results = []
        async for example_result in results:
            aggregated_results.append(example_result)

        for result in aggregated_results:
            print("Input:", result["run"].inputs)
            print("Evaluation Results:", result["evaluation_results"]["results"])
            print("--------------------------------")
        print("Flushing client...")
        t0 = __import__("time").time()
        client.flush()
        print(f"Flush done in {__import__('time').time() - t0:.1f}s")
        return 0

    hard_keys = cfg["hard_gate_keys"]
    total = 0
    passed = 0
    async for example_result in results:
        total += 1
        scores = [
            ev.score
            for ev in example_result["evaluation_results"]["results"]
            if ev.key in hard_keys and ev.score is not None
        ]
        if not scores or (sum(scores) / len(scores)) >= 0.5:
            passed += 1

    pass_rate = passed / total if total else 0.0
    threshold = cfg["threshold"]
    print(f"Pass rate: {passed}/{total} = {pass_rate:.0%} (threshold {threshold:.0%})")

    print("Flushing client...")
    t0 = __import__("time").time()
    client.flush()
    print(f"Flush done in {__import__('time').time() - t0:.1f}s")
    if pass_rate < threshold:
        print("FAIL")
        return 1
    print("PASS")
    return 0


def main() -> None:
    """CLI entry point — parse args, skip marp when DAYTONA_API_KEY is missing."""
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dataset", choices=["ingest", "query", "marp"], required=True)
    p.add_argument("--no-gate", action="store_true",
                   help="Track results without blocking on pass rate (calibration mode)")
    # Cached outputs for evaluator debugging
    p.add_argument("--use-cached-partial-ingest", action="store_true",
                   help="Temporarily use cached Paper2Web partial-ingest output for evaluator debugging")
    p.add_argument("--use-cached-full-ingest", action="store_true",
                   help="Temporarily use cached GraphRAG full-ingest output for evaluator debugging")
    p.add_argument("--use-cached-attention-query", action="store_true",
                   help="Temporarily use cached attention contribution query output for evaluator debugging")
    p.add_argument("--use-cached-transformer-query", action="store_true",
                   help="Temporarily use cached transformer architecture query output for evaluator debugging")
    p.add_argument("--use-cached-transformer-business-theme-marp", action="store_true",
                   help="Temporarily use cached transformer business theme marp output for evaluator debugging")
    p.add_argument("--use-cached-pug-marp", action="store_true",
                   help="Temporarily use cached pug marp output for evaluator debugging")
    p.add_argument("--use-cached-all", action="store_true",
                   help="Temporarily use cached all outputs for evaluator debugging")
    # Filter type for evaluator debugging
    p.add_argument("--filter-type", help="Run only examples with metadata.type == this value")
    # Filter id for evaluator debugging
    p.add_argument("--filter-id", help="Run only examples with metadata.id == this value")

    args = p.parse_args()
    if args.use_cached_all:
        os.environ["ANY2WIKI_USE_CACHED_PARTIAL_INGEST"] = "1"
        os.environ["ANY2WIKI_USE_CACHED_FULL_INGEST"] = "1"
        os.environ["ANY2WIKI_USE_CACHED_ATTENTION_CONTRIBUTION_QUERY"] = "1"
        os.environ["ANY2WIKI_USE_CACHED_TRANSFORMER_ARCHITECTURE_QUERY"] = "1"
        os.environ["ANY2WIKI_USE_CACHED_TRANSFORMER_BUSINESS_THEME_MARP"] = "1"
        os.environ["ANY2WIKI_USE_CACHED_PUG_MARP"] = "1"
    if args.use_cached_partial_ingest:
        os.environ["ANY2WIKI_USE_CACHED_PARTIAL_INGEST"] = "1"
    if args.use_cached_full_ingest:
        os.environ["ANY2WIKI_USE_CACHED_FULL_INGEST"] = "1"
    if args.use_cached_attention_query:
        os.environ["ANY2WIKI_USE_CACHED_ATTENTION_CONTRIBUTION_QUERY"] = "1"
    if args.use_cached_transformer_query:
        os.environ["ANY2WIKI_USE_CACHED_TRANSFORMER_ARCHITECTURE_QUERY"] = "1"
    if args.use_cached_transformer_business_theme_marp:
        os.environ["ANY2WIKI_USE_CACHED_TRANSFORMER_BUSINESS_THEME_MARP"] = "1"
    if args.use_cached_pug_marp:
        os.environ["ANY2WIKI_USE_CACHED_PUG_MARP"] = "1"

    if args.dataset == "marp" and not os.getenv("DAYTONA_API_KEY"):
        print("DAYTONA_API_KEY not set — skipping marp eval (tracking only)")
        sys.exit(0)

    filter_metadata = {}
    filter_metadata.update({"type": args.filter_type} if args.filter_type else {})
    filter_metadata.update({"id": args.filter_id} if args.filter_id else {})

    exit_code = asyncio.run(_run(args.dataset, args.no_gate, filter_metadata=filter_metadata))
    # os._exit skips thread cleanup so LangSmith background sync threads don't hang the process.
    os._exit(exit_code)


if __name__ == "__main__":
    from src.env import load_env

    load_env()
    main()
