"""Regression smoke tests — pytest-langsmith CI gate.

Three tests:
- test_wiki_health_check_runs_clean: runs quick_wiki_integrity_check against the
  real wiki/ dir and asserts no errors. Cheap, always-on.
- test_pdf_parse_produces_content: fetches a known paper and parses it, asserts
  the raw parse output is non-empty and substantial. Does NOT check wiki structure
  (wikilinks are added by the agent, not by parse). Marked @pytest.mark.slow.
- test_existing_wiki_pages_quality: reads a fixed set of known-good wiki pages and
  checks structural invariants with expect(). Cheap, always-on.

Run:
    LANGSMITH_TEST_SUITE="paper2wiki regression" pytest tests/test_regression.py
    LANGSMITH_TEST_SUITE="paper2wiki regression" pytest tests/test_regression.py -m "not slow"

Cache LLM calls in CI:
    LANGSMITH_TEST_CACHE=tests/cassettes pytest tests/test_regression.py
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from langsmith import expect, testing as t

import src.tools.wiki_integrity_check as wiki_check
from src.tools.arxiv_tool import fetch_arxiv
from src.tools.docling_parser import parse_pdf_docling

# Fixed set of known-good pages to check quality on.
# Keep this list small — these are structural smoke checks, not a full audit.
_QUALITY_PAGES = [
    "concepts/transformer-architecture.md",
    "concepts/multi-head-attention.md",
    "concepts/self-attention.md",
]


# ---------------------------------------------------------------------------
# Wiki health smoke test — cheap, always runs
# ---------------------------------------------------------------------------

@pytest.mark.langsmith
@pytest.mark.smoke
def test_wiki_health_check_runs_clean() -> None:
    """Assert the live wiki/ dir passes integrity check with zero errors."""
    t.log_inputs({"wiki_dir": str(wiki_check.WIKI_ROOT)})

    result = wiki_check.run_wiki_integrity_check()
    t.log_outputs({"result": result})

    error_matches = re.findall(r"(\d+) error", result)
    error_count = int(error_matches[0]) if error_matches else 0
    t.log_feedback(key="error_count", score=error_count)

    assert result == "wiki-check: OK", f"Wiki has integrity errors:\n{result}"


# ---------------------------------------------------------------------------
# Parse quality smoke test — slow, opt-in
# ---------------------------------------------------------------------------

@pytest.mark.langsmith
@pytest.mark.slow
def test_pdf_parse_produces_content() -> None:
    """Fetch a known paper and assert the raw parse output is non-empty and substantial.

    Only tests fetch_arxiv + parse_pdf_docling — NOT the agent. Wikilinks and
    wiki structure are added by the agent using the llm-wiki skill; asserting on
    them here would be testing the wrong thing.
    """
    import time
    paper_id = "1706.03762"  # Attention Is All You Need — stable, well-known
    t.log_inputs({"paper_id": paper_id})

    arxiv_result = fetch_arxiv.invoke({"query": paper_id})
    assert not arxiv_result.get("error"), f"fetch_arxiv failed: {arxiv_result.get('error')}"

    start = time.perf_counter()
    parse_result = parse_pdf_docling.invoke({"path": arxiv_result["pdf_path"]})
    elapsed = time.perf_counter() - start

    content: str = parse_result.get("markdown", "") if isinstance(parse_result, dict) else str(parse_result)

    t.log_outputs({"content_length": len(content), "has_headers": "##" in content})
    t.log_feedback(key="content_length", score=len(content))
    t.log_feedback(key="has_headers", score=1 if "##" in content else 0)

    expect.value(elapsed).to_be_less_than(180)                          # parse should finish in 3 min
    assert len(content) > 500, f"Parsed output suspiciously short ({len(content)} chars)"
    assert "##" in content, "No markdown headers in parsed output — parse may have failed"


# ---------------------------------------------------------------------------
# Existing wiki page quality — cheap, always runs
# ---------------------------------------------------------------------------

@pytest.mark.langsmith
@pytest.mark.smoke
@pytest.mark.parametrize("page_path", _QUALITY_PAGES)
def test_existing_wiki_pages_quality(page_path: str) -> None:
    """Check structural invariants on a fixed set of known-good wiki pages.

    Uses expect() for numeric thresholds (hard gate) and t.log_feedback() for
    soft trend tracking. No agent call — reads files directly.
    """
    full_path = wiki_check.WIKI_ROOT / page_path
    t.log_inputs({"page": page_path})

    assert full_path.exists(), f"Wiki page missing: {page_path}"
    content = full_path.read_text(encoding="utf-8")
    t.log_outputs({"content_length": len(content)})

    wikilink_count = content.count("[[")
    header_count = len(re.findall(r"^##", content, re.MULTILINE))

    # soft tracking
    t.log_feedback(key="wikilink_count", score=wikilink_count)
    t.log_feedback(key="header_count", score=header_count)

    # hard gates via expect()
    expect(content).to_contain("[[")                                    # at least one wikilink
    expect(content).to_contain("---")                                   # has frontmatter
    expect.value(wikilink_count).to_be_greater_than(0)                  # numeric threshold
    expect.value(len(content)).to_be_greater_than(200)                  # not a stub
