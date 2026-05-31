"""Web tools package — search and extract via pluggable providers."""

from src.tools.web_tools.tools import web_search, web_extract
from src.tools.web_tools.types import SearchResult, ExtractResult
from src.tools.web_tools.registry import registry  # noqa: F401 — exposed for patching in tests
from src.tools.web_tools import summarizer  # noqa: F401 — exposed for patching in tests

__all__ = [
    "web_search",
    "web_extract",
    "SearchResult",
    "ExtractResult",
]
