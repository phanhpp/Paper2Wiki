"""
types.py — Data contracts for the web tools package.

Every provider implements SearchProvider.
Every consumer works with SearchResult / ExtractResult.
Nothing in this file does I/O or imports vendor SDKs.
We centralize it here so providers and consumers share one contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass
class SearchResult:
    """One search hit — metadata only, no page content.

    """

    title: str
    url: str
    description: str
    position: int = 0


@dataclass
class ExtractResult:
    """One extracted page — raw or summarized content.

    After summarization, content holds the summary and raw_content
    holds the original. Before summarization, raw_content is None.
    """

    url: str
    title: str
    content: str
    error: str | None = None
    raw_content: str | None = None


@runtime_checkable
class SearchProvider(Protocol):
    """Interface that every provider must implement.

    Capability flags tell the registry which operations this provider
    supports. The registry never dispatches to a provider that
    advertises supports_X = False.

    is_available() must be cheap — no network calls. It runs on every
    dispatch to check if credentials exist.
    """

    name: str
    supports_search: bool
    supports_extract: bool

    def is_available(self) -> bool:
        """Return True when this provider's credentials are configured."""
        ...

    def search(self, query: str, limit: int = 5, **kwargs) -> list[SearchResult]:
        """Search the web. Returns metadata only."""
        ...

    async def extract(self, urls: list[str]) -> list[ExtractResult]:
        """Extract page content from URLs."""
        ...
