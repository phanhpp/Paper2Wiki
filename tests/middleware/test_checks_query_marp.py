"""Unit tests for the query and marp check modules.

Pure functions over a hand-built RunContext — no agent, no network. Each check
gets a passing case and its own seeded defect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.middleware.checks import marp, query
from src.middleware.types import RunContext

pytestmark = pytest.mark.unit

QUERY_PAGE = """---
title: What is attention?
created: 2026-01-01
updated: 2026-01-01
type: query
tags: [architecture]
sources: [concepts/self-attention.md]
---

Answer body.
"""

DECK = """---
marp: true
theme: default
---

<!-- _class: lead -->
# Title

---

## Second slide

- point

---

## Third slide

- point
"""


# --- query ------------------------------------------------------------------

@pytest.fixture
def wiki(tmp_path: Path) -> Path:
    (tmp_path / "concepts").mkdir()
    (tmp_path / "queries").mkdir()
    (tmp_path / "concepts" / "self-attention.md").write_text("body", encoding="utf-8")
    (tmp_path / "concepts" / "transformer-architecture.md").write_text("body", encoding="utf-8")
    (tmp_path / "index.md").write_text(
        "# Wiki Index\n\n## Concepts\n[[self-attention]] — attention over one sequence\n",
        encoding="utf-8",
    )
    (tmp_path / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    return tmp_path


def qctx(wiki: Path, **overrides) -> RunContext:
    base = dict(
        paths=["query"],
        writes=[],
        reads=["/wiki/concepts/self-attention.md"],
        tools=["read_file"],
        question="use the wiki: what is self-attention?",
        answer="See [[self-attention]] for details.",
        wiki_root=wiki,
    )
    base.update(overrides)
    return RunContext(**base)


def test_query_clean_run_passes(wiki):
    failed = [c(qctx(wiki)) for c in query.CHECKS]
    assert [r for r in failed if not r.passed] == []


def test_q1_uncited_answer_fails(wiki):
    """The worst failure for a knowledge base: answering from model memory
    while appearing to consult the wiki."""
    result = query.answer_cites_wiki(qctx(wiki, answer="Attention weighs tokens by relevance."))
    assert not result.passed
    assert "cites no [[wikilink]]" in result.gap


def test_q1_citation_to_nonexistent_page_fails(wiki):
    result = query.answer_cites_wiki(qctx(wiki, answer="See [[made-up-page]]."))
    assert not result.passed
    assert "made-up-page" in result.gap


def test_q2_fabricated_citation_fails(wiki):
    """Cited a page it never opened."""
    ctx = qctx(
        wiki,
        answer="See [[transformer-architecture]].",
        reads=["/wiki/concepts/self-attention.md"],
    )
    result = query.cited_pages_were_read(ctx)
    assert not result.passed
    assert "transformer-architecture" in result.gap


def test_q2_silent_when_nothing_was_read(wiki):
    """Q1 already reports that failure; reporting it twice blames it twice."""
    assert query.cited_pages_were_read(qctx(wiki, reads=[])).passed


def test_q3_not_persisting_is_never_a_failure(wiki):
    assert query.saved_answer_is_valid(qctx(wiki, writes=[])).passed


def test_q3_persisted_answer_must_meet_page_standards(wiki):
    (wiki / "queries" / "what-is-attention.md").write_text("no frontmatter", encoding="utf-8")
    ctx = qctx(wiki, writes=["queries/what-is-attention.md", "log.md"])
    result = query.saved_answer_is_valid(ctx)
    assert not result.passed
    assert "frontmatter" in result.gap
    assert "index.md" in result.gap


def test_q3_persisted_answer_needs_a_log_entry(wiki):
    page = wiki / "queries" / "what-is-attention.md"
    page.write_text(QUERY_PAGE, encoding="utf-8")
    (wiki / "index.md").write_text(
        "# Wiki Index\n\n## Queries\n[[what-is-attention]] — summary\n", encoding="utf-8"
    )
    ctx = qctx(wiki, writes=["queries/what-is-attention.md"])  # log.md absent
    result = query.saved_answer_is_valid(ctx)
    assert not result.passed
    assert "log.md" in result.gap


# --- marp -------------------------------------------------------------------

@pytest.fixture
def slides(tmp_path: Path) -> Path:
    root = tmp_path / "marp-slides"
    root.mkdir()
    (root / "deck.md").write_text(DECK, encoding="utf-8")
    return root


def mctx(slides: Path, **overrides) -> RunContext:
    base = dict(
        paths=["marp"],
        writes=[],
        reads=[],
        tools=["task"],
        question="make me a deck about transformers",
        answer="Slides created.",
        wiki_root=slides.parent,
        artifacts=["deck.md"],
        artifacts_root=slides,
    )
    base.update(overrides)
    return RunContext(**base)


def test_marp_clean_deck_passes(slides):
    failed = [c(mctx(slides)) for c in marp.CHECKS]
    assert [r for r in failed if not r.passed] == []


def test_m1_no_deck_downloaded(slides):
    """Built in the sandbox but never downloaded — the user is told slides
    exist while nothing reached the host."""
    result = marp.deck_exists(mctx(slides, artifacts=["deck.pdf"]))
    assert not result.passed
    assert "deck.pdf" in result.gap


def test_m2_missing_marp_directive(slides):
    """Without `marp: true` it renders as plain markdown — the file looks right
    and produces no slides."""
    (slides / "deck.md").write_text(DECK.replace("marp: true\n", ""), encoding="utf-8")
    result = marp.deck_is_marp(mctx(slides))
    assert not result.passed
    assert "deck.md" in result.gap


def test_m3_single_slide_deck(slides):
    (slides / "deck.md").write_text("---\nmarp: true\n---\n\n# Only slide\n", encoding="utf-8")
    result = marp.deck_has_slides(mctx(slides))
    assert not result.passed
    assert "fewer than 2 slides" in result.gap


def test_m4_respects_requested_count(slides):
    """DECK has 3 slides."""
    assert marp.slide_count_matches_request(mctx(slides, question="make 4 slides")).passed
    result = marp.slide_count_matches_request(mctx(slides, question="make me 12 slides"))
    assert not result.passed
    assert "requested ~12" in result.gap


def test_m4_silent_when_no_count_requested(slides):
    assert marp.slide_count_matches_request(mctx(slides, question="make a deck")).passed


@pytest.mark.parametrize(
    "question, expected",
    [
        ("make 12 slides", 12),
        ("a 10-slide deck please", 10),
        ("build me 3 slide summary", 3),
        ("make a deck about transformers", None),
        ("", None),
    ],
)
def test_requested_slide_count(question, expected):
    assert marp.requested_slide_count(question) == expected


def test_slide_count_ignores_frontmatter_delimiters():
    assert marp.slide_count(DECK) == 3
    assert marp.slide_count("") == 0
