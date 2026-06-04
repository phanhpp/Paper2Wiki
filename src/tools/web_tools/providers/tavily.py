"""
tavily.py — Tavily provider (search + extract).

Second priority in the default walk. Good general-purpose search,
extract returns pre-cleaned content.

We skip: _normalize_tavily_documents, _normalize_tavily_search_results,
_tavily_request helpers. Straight SDK calls.

Requires: pip install tavily-python
Env var: TAVILY_API_KEY
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from src.tools.web_tools.types import ExtractResult, SearchResult


class TavilyProvider:
    """Tavily search + extract provider."""

    name = "tavily"
    supports_search = True
    supports_extract = True

    def is_available(self) -> bool:
        return bool(os.getenv("TAVILY_API_KEY", "").strip())

    def _get_client(self) -> Any:
        """Lazy-init Tavily client."""
        from tavily import TavilyClient

        return TavilyClient(api_key=os.getenv("TAVILY_API_KEY", "").strip())

    # Tavily has no academic category — only "news" and "finance" map cleanly.
    _CATEGORY_MAP: dict[str, str] = {
        "news":    "news",
        "finance": "finance",
    }

    def search(self, query: str, limit: int = 3, **kwargs) -> list[SearchResult]:
        """Search via Tavily. Sync."""
        client = self._get_client()
        call_kwargs: dict = {"query": query, "max_results": limit}
        cat = kwargs.get("category")
        if cat:
            topic = self._CATEGORY_MAP.get(cat)
            if topic:
                call_kwargs["topic"] = topic
        raw = client.search(**call_kwargs)

        results = []
        for i, item in enumerate(raw.get("results", [])):
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                description=item.get("content", ""),  # Tavily uses "content" for snippet
                position=i + 1,
            ))
        return results

    async def extract(self, urls: list[str]) -> list[ExtractResult]:
        """Extract via Tavily. Sync SDK, run in thread."""
        client = self._get_client()

        try:
            raw = await asyncio.to_thread(client.extract, urls=urls)
            results = []
            for item in raw.get("results", []):
                results.append(ExtractResult(
                    url=item.get("url", ""),
                    title="",  # Tavily extract doesn't return titles
                    content=item.get("raw_content", "") or item.get("text", ""),
                ))

            # Add failed URLs as errors
            for fail in raw.get("failed_results", []):
                results.append(ExtractResult(
                    url=fail.get("url", ""),
                    title="",
                    content="",
                    error=f"Tavily extract failed: {fail.get('error', 'unknown')}",
                ))
            return results

        except Exception as e:
            # If the whole call fails, return errors for all URLs
            return [
                ExtractResult(url=u, title="", content="",
                              error=f"Tavily extract failed: {str(e)[:200]}")
                for u in urls
            ]
