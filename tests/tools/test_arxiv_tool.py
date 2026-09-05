"""Unit tests for ``fetch_arxiv`` with mocked network and isolated temp dirs.

All tests monkey-patch ``arxiv.Client`` with a fake that returns canned ``_Paper``
objects and never opens a socket. This makes them fast and deterministic, but means
they do NOT catch: real arXiv API changes, network errors, rate-limit handling,
actual PDF download failures, or broken PDF path construction against live data.

For live-network coverage see ``test_regression.py::test_fetch_arxiv_downloads_paper``
(marked ``integration``).

Covered cases:
- Cache hit: valid cache JSON + PDF on disk → returns payload, never constructs Client
- Cache miss (stale): cache JSON exists but PDF is gone → re-fetches and downloads fresh PDF
- URL input: ``arxiv.org/abs/...`` URL → ID extracted, paper fetched, PDF downloaded
- No results: arXiv returns empty list → returns ``{"error": "not_found", "query": ...}``
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.tools.arxiv_tool as arxiv_tool


@pytest.mark.unit
def test_fetch_arxiv_returns_cache_hit_without_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When cache JSON exists and PDF path is valid, return payload without calling arXiv."""
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"pdf")
    cache_payload = {
        "title": "Cached Paper",
        "authors": ["A"],
        "pdf_path": str(pdf_path),
        "metadata": {"arxiv_id": "1706.03762"},
    }

    monkeypatch.setattr(arxiv_tool, "_ARXIV_CACHE_DIR", tmp_path / ".cache")
    monkeypatch.setattr(arxiv_tool, "RAW_PAPERS_DIR", tmp_path / "raw")
    cache_file = arxiv_tool._arxiv_cache_path("1706.03762")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(cache_payload), encoding="utf-8")

    class _FailClient:
        def __init__(self, *args, **kwargs):  # pragma: no cover - should not be called
            raise AssertionError("network client should not be created on cache hit")

    monkeypatch.setattr(arxiv_tool.arxiv, "Client", _FailClient)
    out = arxiv_tool.fetch_arxiv.invoke({"query": "1706.03762"})
    assert out == cache_payload


@pytest.mark.unit
def test_fetch_arxiv_parses_abs_url_and_downloads_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``arxiv.org/abs/...`` URL resolves to ID, downloads via mocked client, returns metadata."""
    monkeypatch.setattr(arxiv_tool, "_ARXIV_CACHE_DIR", tmp_path / ".cache")
    monkeypatch.setattr(arxiv_tool, "RAW_PAPERS_DIR", tmp_path / "raw" / "papers")

    class _Paper:
        title = "Attention Is All You Need"
        authors = [SimpleNamespace(name="Alice"), SimpleNamespace(name="Bob")]
        doi = "10.1000/test"
        published = SimpleNamespace(date=lambda: "2017-01-01")
        updated = SimpleNamespace(date=lambda: "2017-01-02")
        categories = ["cs.CL"]
        entry_id = "https://arxiv.org/abs/1706.03762"

        def get_short_id(self) -> str:
            return "1706.03762"

        def download_pdf(self, filename: str) -> None:
            Path(filename).write_bytes(b"%PDF")

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def results(self, search):
            return [_Paper()]

    monkeypatch.setattr(arxiv_tool.arxiv, "Client", _Client)

    out = arxiv_tool.fetch_arxiv.invoke({"query": "https://arxiv.org/abs/1706.03762"})
    assert out["title"] == "Attention Is All You Need"
    assert Path(out["pdf_path"]).exists()
    assert out["metadata"]["arxiv_id"] == "1706.03762"


@pytest.mark.unit
def test_fetch_arxiv_ignores_cache_when_pdf_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify stale arXiv cache entries do not mask missing downloaded PDFs.

    Metadata alone is not enough for ingestion; if the cached ``pdf_path`` is
    gone, the tool must re-query arXiv and download a fresh PDF.
    """
    monkeypatch.setattr(arxiv_tool, "_ARXIV_CACHE_DIR", tmp_path / ".cache")
    monkeypatch.setattr(arxiv_tool, "RAW_PAPERS_DIR", tmp_path / "raw" / "papers")
    cache_file = arxiv_tool._arxiv_cache_path("1706.03762")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(
        json.dumps(
            {
                "title": "Stale Cached Paper",
                "authors": ["A"],
                "pdf_path": str(tmp_path / "missing.pdf"),
                "metadata": {"arxiv_id": "1706.03762"},
            }
        ),
        encoding="utf-8",
    )

    class _Paper:
        title = "Fresh Paper"
        authors = [SimpleNamespace(name="Alice")]
        doi = None
        published = SimpleNamespace(date=lambda: "2017-01-01")
        updated = SimpleNamespace(date=lambda: "2017-01-02")
        categories = ["cs.CL"]
        entry_id = "https://arxiv.org/abs/1706.03762"

        def get_short_id(self) -> str:
            return "1706.03762"

        def download_pdf(self, filename: str) -> None:
            Path(filename).write_bytes(b"%PDF")

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def results(self, search):
            return [_Paper()]

    monkeypatch.setattr(arxiv_tool.arxiv, "Client", _Client)

    out = arxiv_tool.fetch_arxiv.invoke({"query": "1706.03762"})

    assert out["title"] == "Fresh Paper"
    assert Path(out["pdf_path"]).exists()


@pytest.mark.unit
def test_fetch_arxiv_returns_not_found_when_no_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a miss from arXiv returns a structured not_found dict instead of crashing.

    The agent can inspect result["error"] == "not_found" and report gracefully
    rather than having to catch an exception.
    """
    monkeypatch.setattr(arxiv_tool, "_ARXIV_CACHE_DIR", tmp_path / ".cache")
    monkeypatch.setattr(arxiv_tool, "RAW_PAPERS_DIR", tmp_path / "raw" / "papers")

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def results(self, search):
            return []

    monkeypatch.setattr(arxiv_tool.arxiv, "Client", _Client)

    result = arxiv_tool.fetch_arxiv.invoke({"query": "1706.03762"})
    assert result["error"] == "not_found"
    assert "query" in result
