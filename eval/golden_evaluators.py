"""
Golden dataset evaluators — code evaluators + LLM judges for ingest, query, marp.

Used by ``eval/run_weekly_eval.py`` via LangSmith ``aevaluate``. Each evaluator
returns ``{"key": str, "score": float, "comment": str}``.

LangSmith signatures (pick one per function):
    (outputs) -> dict
    (inputs, outputs) -> dict
    (inputs, outputs, reference_outputs) -> dict
    (run: Run, example: Example) -> dict   ← when example.metadata is needed

LLM judges call Sonnet 4.6 via ``eval.eval_utils.llm_judge()`` and expect JSON ``{"score", "reason"}``.

Evaluator catalog
-----------------

Shared (all datasets that run the agent):
    no_crash              Run completed without error.
    trajectory_subsequence
                          Partial credit: mode-appropriate expected calls
                          (metadata["expected_trajectory"]) appear in order in outputs["trajectory"].
                          Mode from PAPER2WIKI_INGEST_MODE > config > "fast".
                          Also emits trajectory_no_forbidden from metadata["forbidden_tools"].
    
Ingest — code:
    min_page_count        ≥ metadata["min_wiki_pages"] article pages written.
    has_wikilinks         wiki_content contains [[...]] links.
    maintenance_files_updated         graph.json and citations.json both written.

Ingest — LLM:
    wiki_faithfulness     LLM judge: ≥⅔ of metadata["expected_concepts"] covered in wiki_content.
    no_hallucination      LLM judge: Output grounded in source, not fabricated.
    ingest_outcome_correct
                          LLM judge: Agent outcome matches metadata["judge_criteria"]["ingest_outcome_correct"].
                          Can be used for any ingest case when fixed code checks are insufficient
                          to judge the case-specific behavior. Criteria defined per case in JSON.
                          Evidence includes request, final response, files_written, and trajectory.

Query — LLM:
    answer_grounded       LLM judge: Answer cites wiki via [[...]] wikilink.
    answer_correctness    LLM judge: ≥⅔ of reference_outputs["expected_concepts"] addressed.

Marp — code:
    has_marp_frontmatter  YAML frontmatter with marp: true.
    has_lead_slide        <!-- _class: lead --> present.
    has_content_slides    ≥3 ``## `` section headings.
    css_embedded          <style> block present.
    file_saved            slide_path exists on disk.
    used_web_search       web_search/web_extract called before marp-slide-creator.

Marp — LLM:
    slide_quality         LLM judge: Deck meets metadata["judge_criteria"]["slide_quality"].

Hard gates (run_weekly_eval.py — mean hard_gate_keys score ≥ 0.5 per example):
    ingest: no_crash 
    query:  no_crash, trajectory_no_forbidden  (score emitted by trajectory_subsequence)
    marp:   has_marp_frontmatter, file_saved
"""

from __future__ import annotations

import re
from pathlib import Path

from langsmith.schemas import Run, Example

from eval.eval_utils import call_matches, get_ingest_mode, llm_judge, message_text

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Shared
# ---------------------------------------------------------------------------

def no_crash(run: Run, example: Example) -> dict:
    """Pass if the target run completed without raising an error."""
    return {"key": "no_crash", "score": 0 if run.error else 1}


def trajectory_subsequence(run: Run, example: Example) -> dict:
    """Score whether the agent followed the expected tool-call sequence.

    Config (example.metadata):
        expected_trajectory — list of expected calls for this case. Pick by ingest mode
            (``quality`` / ``fast`` from env or config) or use ``any`` when mode-agnostic.
        forbidden_tools — tool names that must not appear (second score).

    Each expected call is either a tool name string (``"read_file"``) or a dict:
        ``{"name": "read_file", "args_contains": "/skills/llm-wiki/SKILL.md"}``
    ``args_contains`` is matched as a substring of the recorded args preview.

    Scoring:
        trajectory_subsequence — expected calls must appear in order in run.outputs["trajectory"];
            extra calls before, between, or after are allowed. Score = matched / expected.
        trajectory_no_forbidden — 1 if none of forbidden_tools were used, else 0.

    Returns a list of two LangSmith result dicts (not a single dict).
    """
    print("===Trajectory_subsequence running===")
    outputs = run.outputs or {}
    metadata = example.metadata or {}

    trajectory_ref = metadata.get("expected_trajectory", {})
    actual = outputs.get("trajectory", [])
    actual_tools = [step["name"] for step in actual]
    actual_tool_set = set(actual_tools)

    mode = get_ingest_mode()
    print(f"Ingest Mode: {mode}")
    required = (
        trajectory_ref.get(mode)
        or trajectory_ref.get("any")
        or next(iter(trajectory_ref.values()), [])
    )
    print(f"Reference trajectory: {required}")
    print(f"Actual trajectory: {actual_tools}")

    if not required:
        correct_trajectory_score = 1
        trajectory_comment = "no reference"
    else:
        matched = 0
        actual_idx = 0
        for expected_call in required:
            while actual_idx < len(actual):
                if call_matches(expected_call, actual[actual_idx]):
                    matched += 1
                    actual_idx += 1
                    break
                actual_idx += 1

        missing = required[matched:]
        correct_trajectory_score = matched / len(required)
        trajectory_comment = (
            f"{matched}/{len(required)} matched in order (mode={mode})"
            + (f"; missing: {missing}" if missing else "")
        )

    forbidden = set((example.metadata or {}).get("forbidden_tools", []))
    forbidden_tools_used = forbidden & actual_tool_set
    forbidden_score = 0 if forbidden_tools_used else 1
    forbidden_comment = (
        f"forbidden tools used: {forbidden_tools_used}"
        if forbidden_tools_used
        else "no forbidden tools defined" if not forbidden else ""
    )

    return [
        {
            "key": "trajectory_subsequence",
            "score": correct_trajectory_score,
            "comment": trajectory_comment,
        },
        {
            "key": "trajectory_no_forbidden",
            "score": forbidden_score,
            "comment": forbidden_comment,
        },
    ]


# ---------------------------------------------------------------------------
# INGEST — code evaluators
# ---------------------------------------------------------------------------

def min_page_count(run: Run, example: Example) -> dict:
    """Pass if the run wrote at least example.metadata["min_wiki_pages"] article pages.

    Counts wiki files excluding index.md, log.md, graph.json, and citations.json.
    """
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
    """Pass if wiki_content contains at least one [[...]] wikilink."""
    found = bool(re.search(r'\[\[.+?\]\]', outputs.get("wiki_content", "")))
    return {"key": "has_wikilinks", "score": 1 if found else 0}


def maintenance_files_updated(outputs: dict) -> dict:
    """Pass if the following files were updated:
        - index.md
        - log.md
        - wiki/graph/graph.json 
        - wiki/graph/citations.json 
     """
    paths = outputs.get("files_written", [])
    graph_score = 1 if (any("graph.json" in p for p in paths) and
                  any("citations.json" in p for p in paths)) else 0
    index_score = int(any(p.endswith("wiki/index.md") for p in paths))
    log_score = int(any(p.endswith("wiki/log.md") for p in paths))
    return [{"key": "graph_updated", "score": graph_score},
        {"key": "index_updated", "score": index_score},
        {"key": "log_updated", "score": log_score},
        ]


# ---------------------------------------------------------------------------
# INGEST — LLM judges
# ---------------------------------------------------------------------------

def wiki_faithfulness(run: Run, example: Example) -> dict:
    """LLM judge: wiki article covers ≥⅔ of example.metadata["expected_concepts"].

    Uses files_written paths + final_message as the evidence signal — both are
    short and cheap. File paths embed concept names (wiki/concepts/self-attention.md);
    final_message is the agent's summary of what was created.
    """
    outputs = run.outputs or {}
    concepts = (example.metadata or {}).get("expected_concepts", [])
    if not concepts:
        return {"key": "wiki_faithfulness", "score": 1.0, "comment": "no concepts to check"}

    files = outputs.get("files_written", [])
    final = outputs.get("final_message", "")
    evidence = f"Files written:\n{chr(10).join(files)}\n\nAgent summary:\n{final[:1500]}"

    system = (
        f"You are checking whether a wiki ingest covered the expected concepts.\n"
        f"Expected concepts: {concepts}\n"
        "Evidence: file paths written (concept names appear in paths) and the agent's summary.\n"
        "Score 1 if at least two-thirds of concepts are indicated (flexibly matched), 0 otherwise.\n"
        'Reply with JSON only: {"score": 0|1, "reason": "<15 words>"}'
    )
    return llm_judge(system, evidence, "wiki_faithfulness")


def no_hallucination(inputs: dict, outputs: dict) -> dict:
    """LLM judge: ingest output is grounded in the source, not fabricated."""
    system = (
        "You are checking whether an AI wiki agent fabricated content.\n"
        "Score 1 if the output is grounded and factual, 0 if it invents results, statistics, or claims.\n"
        'Reply with JSON only: {"score": 0|1, "reason": "<15 words>"}'
    )
    content = outputs.get("wiki_content") or outputs.get("final_message", "")
    return llm_judge(system,
                     f"User request: {inputs.get('message', '')}\nOutput: {content[:3000]}",
                     "no_hallucination")


def ingest_outcome_correct(run: Run, example: Example) -> dict:
    """LLM judge: ingest case outcome matches criteria in metadata['judge_criteria']['ingest_outcome_correct'].

    Flexible judge for ingest cases whose success cannot be captured by code evaluators alone —
    e.g. already-ingested detection, plan-only (partial-ingest), and graceful refusal (negative).
    Criteria are defined per case in the JSON, not in the code.

    Evidence: user request, agent final response, files written (if any)
    """
    print("===Ingest outcome correct running===")
    outputs = run.outputs or {}
    inputs = example.inputs or {}
    criteria = (example.metadata or {}).get("judge_criteria", {}).get("ingest_outcome_correct")
    if not criteria:
        return {"key": "ingest_outcome_correct", "score": 1.0, "comment": "no criteria defined"}
    
    final = message_text(outputs.get("final_message", ""))
    print(f"Final message chars: {len(final)}")

    evidence = (
        f"Request: {inputs.get('message', '')}\n"
        f"Agent response: {final}\n"
        f"Files written: {outputs.get('files_written', [])}\n"
    )
    system = (
        "You are a narrow evaluator. Judge ONLY the explicit pass criteria below.\n"
        f"Pass criteria: {criteria}\n"
        "Score 1 if the agent response includes the required items from the criteria\n"
        "Do not infer hidden requirements. Do not penalize extra steps unless they directly "
        "contradict the pass criteria or the response reports files were written when the criteria requires no writes.\n"
        "Score 0 only if a required item is missing or contradicted.\n"
        'Reply with one JSON object only (no markdown): {"score": 1, "reason": "brief explanation"} '
        "Use score 0 or 1."
    )
    return llm_judge(system, evidence, "ingest_outcome_correct")


# ---------------------------------------------------------------------------
# QUERY — LLM judges
# ---------------------------------------------------------------------------

def answer_grounded(inputs: dict, outputs: dict) -> dict:
    """LLM judge: query answer cites wiki pages via at least one [[...]] wikilink."""
    system = (
        "You are checking whether an AI agent's answer cites wiki pages.\n"
        "Score 1 if the answer contains at least one [[...]] wikilink, 0 if it gives information without citing sources.\n"
        'Reply with JSON only: {"score": 0|1, "reason": "<15 words>"}'
    )
    return llm_judge(system, outputs.get("final_message", "")[:3000], "answer_grounded")


def answer_correctness(inputs: dict, outputs: dict, reference_outputs: dict) -> dict:
    """LLM judge: answer addresses ≥⅔ of reference_outputs["expected_concepts"]."""
    concepts = reference_outputs.get("expected_concepts", [])
    if not concepts:
        return {"key": "answer_correctness", "score": 1.0, "comment": "no reference"}
    system = (
        f"You are checking whether an answer covers these concepts: {concepts}\n"
        "Score 1 if at least two-thirds of the concepts are addressed, 0 otherwise.\n"
        'Reply with JSON only: {"score": 0|1, "reason": "<15 words>"}'
    )
    return llm_judge(system, outputs.get("final_message", "")[:3000], "answer_correctness")


# ---------------------------------------------------------------------------
# MARP — code evaluators
# ---------------------------------------------------------------------------

def has_marp_frontmatter(outputs: dict) -> dict:
    """Pass if slide_content starts with YAML frontmatter containing ``marp: true``."""
    content = outputs.get("slide_content", "")
    found = content.lstrip().startswith("---") and "marp: true" in content[:300]
    return {"key": "has_marp_frontmatter", "score": 1 if found else 0}


def has_lead_slide(outputs: dict) -> dict:
    """Pass if slide_content includes a Marp lead slide (``<!-- _class: lead -->``)."""
    found = "<!-- _class: lead -->" in outputs.get("slide_content", "")
    return {"key": "has_lead_slide", "score": 1 if found else 0}


def has_content_slides(outputs: dict) -> dict:
    """Pass if slide_content has at least three ``## `` section headings."""
    headings = re.findall(r'^## ', outputs.get("slide_content", ""), re.MULTILINE)
    score = 1 if len(headings) >= 3 else 0
    return {"key": "has_content_slides", "score": score,
            "comment": f"{len(headings)} '## ' headings found"}


def css_embedded(outputs: dict) -> dict:
    """Pass if slide_content embeds custom styling in a ``<style>`` block."""
    found = "<style>" in outputs.get("slide_content", "")
    return {"key": "css_embedded", "score": 1 if found else 0}


def file_saved(outputs: dict) -> dict:
    """Pass if slide_path is set and the file exists on disk under REPO_ROOT."""
    path = outputs.get("slide_path", "")
    exists = bool(path) and (REPO_ROOT / path.lstrip("/")).exists()
    return {"key": "file_saved", "score": 1 if exists else 0}


def used_web_search(outputs: dict) -> dict:
    """Pass if web_search or web_extract was called before marp-slide-creator."""
    trajectory = outputs.get("trajectory", [])
    web_tools = {"web_search", "web_extract"}
    marp_idx = next(
        (i for i, t in enumerate(trajectory) if t == "marp-slide-creator"),
        len(trajectory),
    )
    found = any(t in web_tools for t in trajectory[:marp_idx])
    return {"key": "used_web_search", "score": 1 if found else 0}


# ---------------------------------------------------------------------------
# MARP — LLM judges
# ---------------------------------------------------------------------------

def slide_quality(run: Run, example: Example) -> dict:
    """LLM judge: Marp deck meets per-case criteria in example.metadata["judge_criteria"]."""
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
    # check if slide_content is a field in outputs
    content = outputs.get("slide_content") or outputs.get("final_message", "")
    return llm_judge(system,
                     f"Request: {inputs.get('message', '')}\nSlides: {content[:4000]}",
                     "slide_quality")
