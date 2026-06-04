"""
exa.py — Exa provider (search + extract).

Third priority in the default walk. Exa is strong for academic/technical
content which makes it relevant for Paper2Wiki.

We skip: _get_exa_client cache slot. Straight SDK calls.

Requires: pip install exa-py
Env var: EXA_API_KEY
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from src.tools.web_tools.types import ExtractResult, SearchResult


class ExaProvider:
    """Exa search + extract provider."""

    name = "exa"
    supports_search = True
    supports_extract = True

    def is_available(self) -> bool:
        return bool(os.getenv("EXA_API_KEY", "").strip())

    def _get_client(self) -> Any:
        """Lazy-init Exa client."""
        from exa_py import Exa

        return Exa(api_key=os.getenv("EXA_API_KEY", "").strip())

    # Normalised → exa category string.
    # "research" is the canonical cross-provider value; "research paper" is the exa native.
    _CATEGORY_MAP: dict[str, str] = {
        "research":          "research paper",
        "news":              "news",
        "company":           "company",
        "financial report":  "financial report",
        "personal site":     "personal site",
    }

    def search(self, query: str, limit: int = 3, **kwargs) -> list[SearchResult]:
        """Search via Exa. Sync."""
        client = self._get_client()
        call_kwargs: dict = {"num_results": limit, "contents": {"highlights": True}}
        cat = kwargs.get("category")
        if cat:
            exa_cat = self._CATEGORY_MAP.get(cat, cat)
            call_kwargs["category"] = exa_cat
        raw = client.search(query, **call_kwargs)

        results = []
        for i, item in enumerate(raw.results):
            highlights = getattr(item, "highlights", None) or []
            results.append(SearchResult(
                title=getattr(item, "title", "") or "",
                url=getattr(item, "url", "") or "",
                description=" ".join(highlights),
                position=i + 1,
            ))
        return results

    async def extract(self, urls: list[str]) -> list[ExtractResult]:
        """Extract via Exa get_contents. Sync SDK, run in thread."""
        client = self._get_client()

        try:
            # text=True required — omitting it returns no content
            raw = await asyncio.to_thread(client.get_contents, urls, text=True)

            # Build a lookup from per-URL statuses for error reporting
            status_by_id = {}
            for s in getattr(raw, "statuses", []):
                status_by_id[getattr(s, "id", "")] = s

            results = []
            result_urls = {getattr(item, "url", "") for item in raw.results}

            for item in raw.results:
                url = getattr(item, "url", "") or ""
                status = status_by_id.get(url)
                if status and getattr(status, "status", "") == "error":
                    error_obj = getattr(status, "error", None)
                    tag = getattr(error_obj, "tag", "unknown") if error_obj else "unknown"
                    results.append(ExtractResult(url=url, title="", content="",
                                                 error=f"Exa extract failed: {tag}"))
                else:
                    results.append(ExtractResult(
                        url=url,
                        title=getattr(item, "title", "") or "",
                        content=getattr(item, "text", "") or "",
                    ))

            # Add errors for URLs that didn't appear in results at all
            for url in urls:
                if url not in result_urls:
                    status = status_by_id.get(url)
                    error_obj = getattr(status, "error", None) if status else None
                    tag = getattr(error_obj, "tag", "not returned") if error_obj else "not returned"
                    results.append(ExtractResult(url=url, title="", content="",
                                                 error=f"Exa extract failed: {tag}"))
            return results

        except Exception as e:
            return [
                ExtractResult(url=u, title="", content="",
                              error=f"Exa extract failed: {str(e)[:200]}")
                for u in urls
            ]
