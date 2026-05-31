"""Regression tests — pytest-langsmith CI gate.

Tests here hit real files or real external services. They complement the unit tests
in test_arxiv_tool.py / test_wiki_integrity_check.py (which mock all I/O) by catching
the class of failure that mocks can't: external API changes, wiki pages going stale,
Docling output format drift, arXiv schema changes.

See CLAUDE.md ## Testing Strategy for the full rationale.

Tests and their markers:

  test_wiki_health_check_runs_clean       smoke, langsmith
    Runs quick_wiki_integrity_check against the real wiki/ dir.
    Hard gate: result == "wiki-check: OK". Soft: error_count.
    Cheap and always-on — catches broken wikilinks or bad frontmatter committed to wiki/.

  test_fetch_arxiv_downloads_paper        integration, langsmith
    Hits the real arXiv API and downloads a PDF to tmp_path (not wiki/raw/).
    Hard gates: no error, title present, PDF exists on disk. Soft: fetch_latency_s.
    Unit tests mock arxiv.Client entirely — this is the only test that exercises
    the live network path and catches API contract changes.

  test_pdf_parse_produces_content         slow, langsmith
    Fetches the same known paper (cache hit after fetch test) and runs Docling on it.
    Hard gates: output > 500 chars, contains ## headers, parse finishes < 3 min.
    Does NOT assert wikilinks — those are written by the agent, not the parser.
    Marked slow because Docling can take 1–3 minutes.

  test_existing_wiki_pages_quality        smoke, langsmith, parametrized
    Reads a fixed set of known-good wiki pages directly from disk (no agent call).
    Hard gates via expect(): each page has [[wikilinks]], frontmatter, > 200 chars.
    Soft: wikilink_count, header_count tracked as trends in LangSmith.

CI jobs:
  regression job runs: langsmith and not slow  →  wiki health + page quality
  slow / integration:  opt-in locally only

Run:
    LANGSMITH_TEST_SUITE="paper2wiki-regression" uv run pytest tests/test_regression.py -m "langsmith and not slow" -q

Dry run (no LangSmith sync):
    LANGSMITH_TEST_TRACKING=false uv run pytest tests/test_regression.py -v -s -m "not slow"
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from langsmith import expect, testing as t

import src.tools.wiki_integrity_check as wiki_check
import src.tools.arxiv_tool as arxiv_tool
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
# arXiv fetch smoke test — fast, always runs
# ---------------------------------------------------------------------------

@pytest.mark.langsmith
@pytest.mark.integration
def test_fetch_arxiv_downloads_paper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fetch a known paper from arXiv and assert metadata + PDF are present.

    Complements the unit tests in test_arxiv_tool.py, which mock arxiv.Client and
    never touch the network. This test hits the real arXiv API and downloads a real
    PDF, so it catches regressions that mocks can't: API schema changes, auth errors,
    download failures, or broken PDF path construction.

    Downloads go to tmp_path instead of wiki/raw/ so repeated runs don't accumulate
    test artifacts in the production wiki folder.
    Marked integration (not smoke) because it requires external network access.
    """
    import time

    monkeypatch.setattr(arxiv_tool, "RAW_PAPERS_DIR", tmp_path / "papers")
    monkeypatch.setattr(arxiv_tool, "_ARXIV_CACHE_DIR", tmp_path / ".cache")

    paper_id = "1706.03762"  # Attention Is All You Need — stable, well-known
    t.log_inputs({"paper_id": paper_id})

    start = time.perf_counter()
    result = fetch_arxiv.invoke({"query": paper_id})
    elapsed = time.perf_counter() - start

    t.log_outputs({"title": result.get("title"), "pdf_path": result.get("pdf_path")})
    t.log_feedback(key="fetch_latency_s", score=elapsed)

    assert not result.get("error"), f"fetch_arxiv errored: {result.get('error')}"
    assert result.get("title"), "No title in result"
    assert result.get("pdf_path"), "No pdf_path in result"
    assert Path(result["pdf_path"]).exists(), f"PDF not on disk: {result['pdf_path']}"


# ---------------------------------------------------------------------------
# Docling parse smoke test — slow, opt-in
# ---------------------------------------------------------------------------

@pytest.mark.langsmith
@pytest.mark.slow
def test_pdf_parse_produces_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Parse a known PDF with Docling and assert the output is non-empty and structured.

    Downloads go to tmp_path, not wiki/raw/ — keeps the production wiki clean.
    Does NOT check for wikilinks — those are written by the agent, not the parser.
    """
    import time

    monkeypatch.setattr(arxiv_tool, "RAW_PAPERS_DIR", tmp_path / "papers")
    monkeypatch.setattr(arxiv_tool, "_ARXIV_CACHE_DIR", tmp_path / ".cache")

    paper_id = "1706.03762"
    arxiv_result = fetch_arxiv.invoke({"query": paper_id})
    assert not arxiv_result.get("error"), f"fetch_arxiv failed: {arxiv_result.get('error')}"

    pdf_path = arxiv_result["pdf_path"]
    t.log_inputs({"pdf_path": pdf_path})

    start = time.perf_counter()
    parse_result = parse_pdf_docling.invoke({"path": pdf_path})
    elapsed = time.perf_counter() - start

    content: str = parse_result.get("markdown", "") if isinstance(parse_result, dict) else str(parse_result)

    t.log_outputs({"content_length": len(content), "has_headers": "##" in content})
    t.log_feedback(key="content_length", score=len(content))
    t.log_feedback(key="has_headers", score=1 if "##" in content else 0)

    expect.value(elapsed).to_be_less_than(180)
    assert len(content) > 500, f"Parsed output suspiciously short ({len(content)} chars)"
    assert "##" in content, "No markdown headers — parse may have failed"


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

    if not full_path.exists():
        pytest.skip(f"Wiki page not on disk (wiki/ not committed): {page_path}")
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
