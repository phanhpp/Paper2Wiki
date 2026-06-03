"""
firecrawl.py — Firecrawl provider (search + extract).

Highest priority in the default walk because it handles both search
and extract well, including PDF→markdown conversion server-side.

Hermes equivalent: plugins/web/firecrawl/provider.py
We skip: _FirecrawlProxy lazy loading, gateway auth, dual-auth config,
response-shape normalizers. Straight SDK calls.

Requires: pip install firecrawl-py
Env var: FIRECRAWL_API_KEY
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from src.tools.web_tools.types import ExtractResult, SearchResult


class FirecrawlProvider:
    """Firecrawl search + extract provider."""

    name = "firecrawl"
    supports_search = True
    supports_extract = True

    def is_available(self) -> bool:
        return bool(os.getenv("FIRECRAWL_API_KEY", "").strip())

    def _get_client(self) -> Any:
        from firecrawl import Firecrawl

        return Firecrawl(api_key=os.getenv("FIRECRAWL_API_KEY", "").strip())

    # Maps the normalized category value to the firecrawl API param.
    # "research"/"github"/"pdf" go via categories=[]; "news" goes via sources=[].
    _CATEGORY_MAP: dict[str, dict] = {
        "research": {"categories": ["research"]},
        "github":   {"categories": ["github"]},
        "pdf":      {"categories": ["pdf"]},
        "news":     {"sources": ["news"]},
    }

    def search(self, query: str, limit: int = 3, **kwargs) -> list[SearchResult]:
        """Search via Firecrawl. Sync."""
        client = self._get_client()
        call_kwargs: dict = {"limit": limit}
        cat = kwargs.get("category")
        if cat:
            call_kwargs.update(self._CATEGORY_MAP.get(cat, {}))
        raw = client.search(query, **call_kwargs)

        results = []
        for i, item in enumerate(raw.web or []):
            results.append(SearchResult(
                title=getattr(item, "title", "") or "",
                url=getattr(item, "url", "") or "",
                description=getattr(item, "description", "") or "",
                position=i + 1,
            ))
        return results

    async def extract(self, urls: list[str]) -> list[ExtractResult]:
        """Extract page content via Firecrawl scrape. Async (threaded)."""
        client = self._get_client()

        async def scrape_one(url: str) -> ExtractResult:
            try:
                # v2 SDK: scrape(url, formats=[...]) returns an object, not a dict
                raw = await asyncio.to_thread(
                    client.scrape, url, formats=["markdown"]
                )
                content = getattr(raw, "markdown", "") or ""
                metadata = getattr(raw, "metadata", None)
                title = getattr(metadata, "title", "") or "" if metadata else ""
                return ExtractResult(url=url, title=title, content=content)
            except Exception as e:
                return ExtractResult(
                    url=url, title="", content="",
                    error=f"Firecrawl extract failed: {str(e)[:200]}",
                )

        return list(await asyncio.gather(*[scrape_one(u) for u in urls]))
