"""Unit tests for the Loop 2 check layer.

Pure functions over a hand-built RunContext and a temp wiki — no agent, no
graph, no network. Each ingest check gets a passing case and its own seeded
defect, so a failure names which check regressed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.middleware.checks import ingest
from src.middleware.classify import asked_for_wiki, classify
from src.middleware.feedback import explain, feedback_text
from src.middleware.types import CheckResult, RunContext

pytestmark = pytest.mark.unit

PAGE = """---
title: Self-Attention
created: 2026-01-01
updated: 2026-01-01
type: concept
tags: [architecture]
sources: [raw/papers/attention.pdf]
---

Relates to [[transformer-architecture]].
"""

INDEX = """# Wiki Index

## Concepts
[[self-attention]] — Attention over a single sequence
[[transformer-architecture]] — Encoder-decoder stack built from attention
"""

GRAPH = {
    "nodes": [
        {"id": "self-attention", "type": "concept", "label": "Self-Attention",
         "path": "concepts/self-attention.md", "tags": ["architecture"]},
        {"id": "transformer-architecture", "type": "concept", "label": "Transformer",
         "path": "concepts/transformer-architecture.md", "tags": ["architecture"]},
    ],
    "edges": [
        {"source": "transformer-architecture", "target": "self-attention",
         "relation": "uses", "confidence": "EXTRACTED"},
    ],
}


@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    """A minimal wiki where every ingest check passes."""
    (tmp_path / "concepts").mkdir()
    (tmp_path / "graph").mkdir()
    (tmp_path / "raw" / "papers").mkdir(parents=True)

    (tmp_path / "concepts" / "self-attention.md").write_text(PAGE, encoding="utf-8")
    (tmp_path / "concepts" / "transformer-architecture.md").write_text(PAGE, encoding="utf-8")
    (tmp_path / "index.md").write_text(INDEX, encoding="utf-8")
    (tmp_path / "log.md").write_text("# Wiki Log\n\n## [2026-01-01] ingest | self-attention\n", encoding="utf-8")
    (tmp_path / "graph" / "graph.json").write_text(json.dumps(GRAPH), encoding="utf-8")
    (tmp_path / "raw" / "papers" / "attention.pdf").write_bytes(b"%PDF-1.4 fake")
    return tmp_path


def ctx_for(wiki: Path, **overrides) -> RunContext:
    base = dict(
        paths=["ingest"],
        writes=["concepts/self-attention.md", "index.md", "log.md", "graph/graph.json"],
        reads=["skills/llm-wiki/SKILL.md"],
        tools=["write_file"],
        question="ingest the attention paper",
        answer="Done.",
        wiki_root=wiki,
    )
    base.update(overrides)
    return RunContext(**base)


def run_all(ctx: RunContext) -> dict[str, CheckResult]:
    return {c(ctx).id: c(ctx) for c in ingest.CHECKS}


# --- happy path -------------------------------------------------------------

def test_clean_ingest_passes_every_check(wiki):
    results = run_all(ctx_for(wiki))
    failed = {cid: r.gap for cid, r in results.items() if not r.passed}
    assert failed == {}, f"expected a clean wiki to pass, got: {failed}"


# --- one seeded defect per check -------------------------------------------

def test_s1_no_page_written(wiki):
    ctx = ctx_for(wiki, writes=["raw/papers/attention.pdf"])
    result = ingest.page_written(ctx)
    assert not result.passed
    assert "raw/papers/attention.pdf" in result.gap


def test_s2_missing_frontmatter_keys(wiki):
    (wiki / "concepts" / "self-attention.md").write_text(
        "---\ntitle: X\ntype: concept\n---\n\nbody\n", encoding="utf-8"
    )
    result = ingest.frontmatter_valid(ctx_for(wiki))
    assert not result.passed
    assert "sources" in result.gap


def test_s3_broken_wikilink(wiki):
    (wiki / "concepts" / "self-attention.md").write_text(
        PAGE.replace("transformer-architecture", "does-not-exist"), encoding="utf-8"
    )
    result = ingest.wikilinks_resolve(ctx_for(wiki))
    assert not result.passed
    assert "does-not-exist" in result.gap


def test_s4_missing_index_entry(wiki):
    (wiki / "index.md").write_text("# Wiki Index\n\n## Concepts\n", encoding="utf-8")
    result = ingest.index_updated(ctx_for(wiki))
    assert not result.passed
    assert "self-attention" in result.gap


def test_s5_log_not_appended(wiki):
    ctx = ctx_for(wiki, writes=["concepts/self-attention.md", "index.md"])
    result = ingest.log_appended(ctx)
    assert not result.passed
    assert "log.md" in result.gap


def test_s6_graph_missing_node(wiki):
    graph = {"nodes": [n for n in GRAPH["nodes"] if n["id"] != "self-attention"],
             "edges": []}
    (wiki / "graph" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    result = ingest.graph_has_node(ctx_for(wiki))
    assert not result.passed
    assert "self-attention" in result.gap


def test_s7_graph_node_isolated(wiki):
    graph = {"nodes": GRAPH["nodes"], "edges": []}
    (wiki / "graph" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    result = ingest.graph_node_connected(ctx_for(wiki))
    assert not result.passed
    assert "self-attention" in result.gap


def test_s8_this_runs_node_points_at_missing_file(wiki):
    """The page was written, but its graph node points somewhere else."""
    graph = {
        "nodes": [
            {"id": "self-attention", "type": "concept", "label": "Self-Attention",
             "path": "concepts/typo-in-path.md"},
        ],
        "edges": GRAPH["edges"],
    }
    (wiki / "graph" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    result = ingest.graph_consistent(ctx_for(wiki))
    assert not result.passed
    assert "self-attention" in result.gap


def test_s8_ignores_pre_existing_dangling_nodes(wiki):
    """Drift the run did not cause must not fail it — otherwise every future
    ingest fails for damage someone else did, and the loop gets switched off."""
    graph = {
        "nodes": GRAPH["nodes"] + [
            {"id": "ghost", "type": "concept", "label": "Ghost", "path": "concepts/ghost.md"}
        ],
        "edges": GRAPH["edges"],
    }
    (wiki / "graph" / "graph.json").write_text(json.dumps(graph), encoding="utf-8")
    result = ingest.graph_consistent(ctx_for(wiki))
    assert result.passed, "a dangling node for an untouched page is not this run's failure"


def test_s9_source_not_on_disk(wiki):
    (wiki / "raw" / "papers" / "attention.pdf").unlink()
    result = ingest.sources_recorded(ctx_for(wiki))
    assert not result.passed
    assert "attention.pdf" in result.gap


def test_malformed_graph_json_is_a_gap_not_a_crash(wiki):
    (wiki / "graph" / "graph.json").write_text("{not json", encoding="utf-8")
    result = ingest.graph_has_node(ctx_for(wiki))
    assert not result.passed          # reported as a gap...
    assert result.gap                 # ...not raised as check_error


# --- classification ---------------------------------------------------------

@pytest.mark.parametrize(
    "writes, tools, question, artifacts, expected",
    [
        (["concepts/x.md"], ["write_file"], "ingest this", [], ["ingest"]),
        (["raw/papers/x.pdf"], ["fetch_arxiv"], "ingest this", [], ["ingest"]),
        ([], [], "thanks!", [], []),
        # marp is detected from marp-slides/, which lives outside the wiki
        (["concepts/x.md"], [], "ingest and make slides", ["deck.md"],
         ["marp", "ingest"]),
        ([], [], "make me some slides", ["deck.md"], ["marp"]),
        ([], [], "use the wiki: what is attention?", [], ["query"]),
        # Re-ingest: tools ran, nothing written. Must NOT be an ingest failure.
        ([], ["fetch_arxiv", "parse_pdf_docling"], "ingest this paper", [], []),
        # A plain web lookup must not look like a failed ingest.
        ([], ["web_search"], "what is the weather", [], []),
    ],
)
def test_classify(writes, tools, question, artifacts, expected):
    assert classify(writes, [], tools, question, artifacts) == expected


@pytest.mark.parametrize(
    "question, expected",
    [
        ("use the wiki to answer this", True),
        ("What do we know about transformers?", True),
        ("check our notes on attention", True),
        ("based on the papers we have ingested, summarise", True),
        ("USE THE WIKI!!!", True),               # normalisation handles case/punctuation
        ("what is an attention mechanism?", False),
        ("hi", False),
        ("", False),
    ],
)
def test_asked_for_wiki(question, expected):
    assert asked_for_wiki(question) is expected


# --- feedback ---------------------------------------------------------------

def test_feedback_names_the_specific_gap(wiki):
    failed = [CheckResult.fail("S6", "graph.json has no node for ['self-attention']")]
    text = feedback_text(failed, ctx_for(wiki))
    assert "S6" in text
    assert "graph.json has no node" in text


def test_feedback_leads_with_skill_hint_when_skill_unread(wiki):
    failed = [CheckResult.fail("S4", "index.md has no entry")]
    text = feedback_text(failed, ctx_for(wiki, reads=[]))
    assert "SKILL.md" in text
    assert text.index("SKILL.md") < text.index("S4")


def test_feedback_omits_skill_hint_when_skill_was_read(wiki):
    failed = [CheckResult.fail("S4", "index.md has no entry")]
    text = feedback_text(failed, ctx_for(wiki))
    assert "SKILL.md" not in text


def test_explain_summarises_counts():
    results = [CheckResult.ok("S1"), CheckResult.fail("S2", "x"), CheckResult.fail("S3", "y")]
    assert explain(results) == "2 of 3 checks failed: S2, S3"
    assert explain([CheckResult.ok("S1")]) == "all 1 checks passed"


def test_skill_hint_is_ingest_only(wiki):
    """On a query run the hint is noise — an agent that cited the wrong page did
    not fail because it skipped the ingest conventions."""
    failed = [CheckResult.fail("Q2", "cited pages never opened: ['x']")]
    query_ctx = ctx_for(wiki, paths=["query"], reads=[])
    assert "SKILL.md" not in feedback_text(failed, query_ctx)

    ingest_ctx = ctx_for(wiki, paths=["ingest"], reads=[])
    assert "SKILL.md" in feedback_text(failed, ingest_ctx)
