"""
Golden dataset evaluators — code evaluators + LLM judges for ingest, query, marp.

All functions follow LangSmith evaluator signatures:
    (outputs) -> dict
    (inputs, outputs) -> dict
    (inputs, outputs, reference_outputs) -> dict
    (run: Run, example: Example) -> dict   ← use when example.metadata is needed

Return dict shape: {"key": str, "score": float, "comment": str}
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import anthropic
from langsmith.schemas import Run, Example

_JUDGE_MODEL = "claude-haiku-4-5-20251001"
REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

def no_crash(run: Run, example: Example) -> dict:
    return {"key": "no_crash", "score": 0 if run.error else 1}


def trajectory_subsequence(outputs: dict, reference_outputs: dict) -> dict:
    """Partial-credit check: are the reference tools a subsequence of actual trajectory?"""
    required = reference_outputs.get("trajectory", [])
    actual = outputs.get("trajectory", [])
    if not required:
        return {"key": "trajectory_subsequence", "score": 1.0, "comment": "no reference"}
    i = j = 0
    while i < len(required) and j < len(actual):
        if required[i] == actual[j]:
            i += 1
        j += 1
    score = round(i / len(required), 2)
    return {"key": "trajectory_subsequence", "score": score,
            "comment": f"{i}/{len(required)} required tools matched"}


# ---------------------------------------------------------------------------
# INGEST — code evaluators
# ---------------------------------------------------------------------------

def index_updated(outputs: dict) -> dict:
    updated = any("index.md" in p for p in outputs.get("files_written", []))
    return {"key": "index_updated", "score": 1 if updated else 0}


def log_updated(outputs: dict) -> dict:
    updated = any("log.md" in p for p in outputs.get("files_written", []))
    return {"key": "log_updated", "score": 1 if updated else 0}


def min_page_count(run: Run, example: Example) -> dict:
    outputs = run.outputs or {}
    min_pages = (example.metadata or {}).get("min_wiki_pages", 1)
    wiki_pages = [
        p for p in outputs.get("files_written", [])
        if not any(x in p for x in ("index.md", "log.md", "graph.json", "citations.json"))
    ]
    score = 1 if len(wiki_pages) >= min_pages else 0
    return {"key": "min_page_count", "score": score,
            "comment": f"{len(wiki_pages)} pages written, need {min_pages}"}


def has_wikilinks(outputs: dict) -> dict:
    found = bool(re.search(r'\[\[.+?\]\]', outputs.get("wiki_content", "")))
    return {"key": "has_wikilinks", "score": 1 if found else 0}


def graph_updated(outputs: dict) -> dict:
    paths = outputs.get("files_written", [])
    score = 1 if (any("graph.json" in p for p in paths) and
                  any("citations.json" in p for p in paths)) else 0
    return {"key": "graph_updated", "score": score}


# ---------------------------------------------------------------------------
# QUERY — code evaluators
# ---------------------------------------------------------------------------

def query_is_read_only(outputs: dict) -> dict:
    write_tools = {"write_file", "edit_file", "execute"}
    used = write_tools & set(outputs.get("trajectory", []))
    return {"key": "query_is_read_only", "score": 0 if used else 1,
            "comment": f"write tools used: {used}" if used else ""}


# ---------------------------------------------------------------------------
# MARP — code evaluators
# ---------------------------------------------------------------------------

def has_marp_frontmatter(outputs: dict) -> dict:
    content = outputs.get("slide_content", "")
    found = content.lstrip().startswith("---") and "marp: true" in content[:300]
    return {"key": "has_marp_frontmatter", "score": 1 if found else 0}


def has_lead_slide(outputs: dict) -> dict:
    found = "<!-- _class: lead -->" in outputs.get("slide_content", "")
    return {"key": "has_lead_slide", "score": 1 if found else 0}


def has_content_slides(outputs: dict) -> dict:
    headings = re.findall(r'^## ', outputs.get("slide_content", ""), re.MULTILINE)
    score = 1 if len(headings) >= 3 else 0
    return {"key": "has_content_slides", "score": score,
            "comment": f"{len(headings)} '## ' headings found"}


def css_embedded(outputs: dict) -> dict:
    found = "<style>" in outputs.get("slide_content", "")
    return {"key": "css_embedded", "score": 1 if found else 0}


def file_saved(outputs: dict) -> dict:
    path = outputs.get("slide_path", "")
    exists = bool(path) and (REPO_ROOT / path.lstrip("/")).exists()
    return {"key": "file_saved", "score": 1 if exists else 0}


def used_web_search(outputs: dict) -> dict:
    trajectory = outputs.get("trajectory", [])
    web_tools = {"web_search", "web_extract"}
    marp_idx = next(
        (i for i, t in enumerate(trajectory) if t == "marp-slide-creator"),
        len(trajectory),
    )
    found = any(t in web_tools for t in trajectory[:marp_idx])
    return {"key": "used_web_search", "score": 1 if found else 0}


# ---------------------------------------------------------------------------
# Shared LLM judge helper
# ---------------------------------------------------------------------------

def _judge(system: str, user_content: str, key: str) -> dict:
    client = anthropic.Anthropic()
    resp = client.messages.create(
        model=_JUDGE_MODEL,
        max_tokens=256,
        system=system,
        messages=[{"role": "user", "content": user_content[:4000]}],
    )
    try:
        result = json.loads(resp.content[0].text)
        return {"key": key, "score": float(result["score"]),
                "comment": result.get("reason", "")}
    except Exception:
        return {"key": key, "score": 0.0, "comment": "parse error"}


# ---------------------------------------------------------------------------
# INGEST — LLM judges
# ---------------------------------------------------------------------------

def wiki_faithfulness(run: Run, example: Example) -> dict:
    outputs = run.outputs or {}
    concepts = (example.metadata or {}).get("expected_concepts", [])
    if not concepts:
        return {"key": "wiki_faithfulness", "score": 1.0, "comment": "no concepts to check"}
    system = (
        f"You are evaluating whether a wiki article covers expected concepts.\n"
        f"Expected concepts: {concepts}\n"
        "Score 1 if at least two-thirds of concepts appear (flexibly matched), 0 otherwise.\n"
        'Reply with JSON only: {"score": 0|1, "reason": "<15 words>"}'
    )
    return _judge(system, outputs.get("wiki_content", "")[:4000], "wiki_faithfulness")


def no_hallucination(inputs: dict, outputs: dict) -> dict:
    system = (
        "You are checking whether an AI wiki agent fabricated content.\n"
        "Score 1 if the output is grounded and factual, 0 if it invents results, statistics, or claims.\n"
        'Reply with JSON only: {"score": 0|1, "reason": "<15 words>"}'
    )
    content = outputs.get("wiki_content") or outputs.get("final_message", "")
    return _judge(system,
                  f"User request: {inputs.get('message', '')}\nOutput: {content[:3000]}",
                  "no_hallucination")


def correctly_refused(inputs: dict, outputs: dict) -> dict:
    system = (
        "You are checking whether an AI agent correctly refused an invalid request.\n"
        "Score 1 if the agent explains it cannot fulfill the request without fabricating content. "
        "Score 0 if it hallucinated wiki pages or invented content.\n"
        'Reply with JSON only: {"score": 0|1, "reason": "<15 words>"}'
    )
    return _judge(system,
                  f"Request: {inputs.get('message', '')}\nAgent: {outputs.get('final_message', '')[:3000]}",
                  "correctly_refused")


def correctly_handled_duplicate(inputs: dict, outputs: dict) -> dict:
    system = (
        "You are checking whether an AI agent detected a duplicate paper and did not overwrite existing wiki pages.\n"
        "Score 1 if the agent detected duplication and informed the user without overwriting. Score 0 if it re-ingested.\n"
        'Reply with JSON only: {"score": 0|1, "reason": "<15 words>"}'
    )
    return _judge(system,
                  f"Request: {inputs.get('message', '')}\n"
                  f"Agent: {outputs.get('final_message', '')[:3000]}\n"
                  f"Files written: {outputs.get('files_written', [])}",
                  "correctly_handled_duplicate")


# ---------------------------------------------------------------------------
# QUERY — LLM judges
# ---------------------------------------------------------------------------

def answer_grounded(inputs: dict, outputs: dict) -> dict:
    system = (
        "You are checking whether an AI agent's answer cites wiki pages.\n"
        "Score 1 if the answer contains at least one [[...]] wikilink, 0 if it gives information without citing sources.\n"
        'Reply with JSON only: {"score": 0|1, "reason": "<15 words>"}'
    )
    return _judge(system, outputs.get("final_message", "")[:3000], "answer_grounded")


def answer_correctness(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    concepts = reference_outputs.get("expected_concepts", [])
    if not concepts:
        return {"key": "answer_correctness", "score": 1.0, "comment": "no reference"}
    system = (
        f"You are checking whether an answer covers these concepts: {concepts}\n"
        "Score 1 if at least two-thirds of the concepts are addressed, 0 otherwise.\n"
        'Reply with JSON only: {"score": 0|1, "reason": "<15 words>"}'
    )
    return _judge(system, outputs.get("final_message", "")[:3000], "answer_correctness")


def query_correctly_handled(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    expected = reference_outputs.get("expected_behaviour", "")
    if not expected:
        return {"key": "correctly_handled", "score": 1.0, "comment": "positive case"}
    criteria = {
        "refuse": "Agent explains the request is out of scope without producing code or fabricating content.",
        "admit_missing": "Agent checks the wiki index, finds no matching page, and tells the user honestly — does not invent content.",
    }.get(expected, expected)
    system = (
        f"Pass criteria: {criteria}\n"
        "Score 1 if criteria is met, 0 otherwise.\n"
        'Reply with JSON only: {"score": 0|1, "reason": "<15 words>"}'
    )
    return _judge(system,
                  f"Request: {inputs.get('message', '')}\nAgent: {outputs.get('final_message', '')[:3000]}",
                  "correctly_handled")


# ---------------------------------------------------------------------------
# MARP — LLM judges
# ---------------------------------------------------------------------------

def slide_quality(run: Run, example: Example) -> dict:
    inputs = example.inputs or {}
    outputs = run.outputs or {}
    criteria = (example.metadata or {}).get("judge_criteria", {}).get(
        "slide_quality", "Slides cover key concepts and are grounded in source material."
    )
    system = (
        f"You are evaluating AI-generated Marp slide quality.\n"
        f"Criteria: {criteria}\n"
        "Score 1 if criteria is met, 0 otherwise.\n"
        'Reply with JSON only: {"score": 0|1, "reason": "<20 words>"}'
    )
    content = outputs.get("slide_content") or outputs.get("final_message", "")
    return _judge(system,
                  f"Request: {inputs.get('message', '')}\nSlides: {content[:4000]}",
                  "slide_quality")
