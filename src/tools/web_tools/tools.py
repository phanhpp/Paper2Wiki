"""
tools.py — LangChain tool definitions for web_search and web_extract.

Flow:
  web_search:  registry.get_search_provider() → provider.search()
  web_extract: security.check_urls() → registry.get_extract_provider()
               → provider.extract() → summarizer.summarize()
"""

from __future__ import annotations

import asyncio
import logging

from langchain_core.tools import tool

from src.tools.web_tools.registry import registry
from src.tools.web_tools.security import check_urls
from src.tools.web_tools.summarizer import summarize
from src.tools.web_tools.types import ExtractResult, SearchResult

logger = logging.getLogger(__name__)


@tool()
def web_search(query: str, limit: int = 5) -> list[SearchResult]:
    """Search the web for information. Returns metadata only (no page content).

    Args:
        query: Search query string.
        limit: Max results (clamped to 1–100).

    Returns:
        List of SearchResult with title, url, description.

    Raises:
        RuntimeError: If no search provider is available.
    """
    limit = max(1, min(int(limit), 100))

    provider = registry.get_search_provider()
    if provider is None:
        available = registry.list_available()
        raise RuntimeError(
            f"No search provider available. "
            f"Set FIRECRAWL_API_KEY, TAVILY_API_KEY, or EXA_API_KEY. "
            f"Currently available: {available or 'none'}"
        )

    logger.info("web_search via %s: '%s' (limit=%d)", provider.name, query, limit)
    return provider.search(query, limit)


@tool()
async def web_extract(
    urls: list[str],
    use_summarizer: bool = True,
    model: str | None = None,
    min_length: int | None = None,
) -> list[ExtractResult]:
    """Extract content from web page URLs with optional LLM summarization.

    Args:
        urls: URLs to extract content from.
        use_summarizer: Whether to LLM-summarize large pages (default True).
        model: Override summarizer model (default from config).
        min_length: Override min content length for summarization.

    Returns:
        List of ExtractResult. Content is summarized if use_summarizer=True
        and page exceeds min_length threshold.

    Raises:
        RuntimeError: If no extract provider is available.
    """
    if not urls:
        return []

    safe_urls, blocked = check_urls(urls)

    results: list[ExtractResult] = [
        ExtractResult(url=b["url"], title="", content="", error=b["error"])
        for b in blocked
    ]

    if not safe_urls:
        return results

    provider = registry.get_extract_provider()
    if provider is None:
        available = registry.list_available()
        raise RuntimeError(
            f"No extract provider available. "
            f"Set FIRECRAWL_API_KEY, TAVILY_API_KEY, or EXA_API_KEY. "
            f"Currently available: {available or 'none'}"
        )

    logger.info("web_extract via %s: %d URL(s)", provider.name, len(safe_urls))
    fetched = await provider.extract(safe_urls)
    results.extend(fetched)

    if use_summarizer:
        async def maybe_summarize(page: ExtractResult) -> None:
            if page.error or not page.content:
                return
            summary = await summarize(
                page.content,
                url=page.url,
                title=page.title,
                model=model,
                min_length=min_length,
            )
            if summary is not None:
                page.raw_content = page.content
                page.content = summary

        gather_results = await asyncio.gather(
            *[maybe_summarize(page) for page in fetched],
            return_exceptions=True,
        )
        for i, result in enumerate(gather_results):
            if isinstance(result, BaseException):
                logger.warning("Summarization failed for %s: %s", fetched[i].url, result)

    return results
