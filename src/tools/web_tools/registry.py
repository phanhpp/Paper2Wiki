"""
registry.py — Picks which provider (Firecrawl / Tavily / Exa) handles each request.

When web_search() or web_extract() is called, the registry resolves the right
provider using a three-level fallback so you can control routing via config
without touching code:

  1. Per-capability override  — web.search_backend / web.extract_backend in config.yaml
  2. Shared default           — web.backend in config.yaml
  3. Priority walk            — first of [firecrawl, tavily, exa] with valid credentials

Config file: ~/.paper2wiki/config.yaml  (copy from config.example.yaml in repo root)
Override path via: PAPER2WIKI_CONFIG=/path/to/config.yaml

Example config.yaml:
    web:
      backend: firecrawl          # default for both search and extract
      search_backend: tavily      # override search only
      extract_backend: firecrawl  # override extract only

If no config exists, the priority walk kicks in automatically — just set whichever
API key env var you have (FIRECRAWL_API_KEY / TAVILY_API_KEY / EXA_API_KEY) and
the registry picks the first available provider.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from src.tools.web_tools.types import SearchProvider

logger = logging.getLogger(__name__)

# Default provider priority — same order as 
# First available wins when no config override is set.
DEFAULT_PRIORITY = ["firecrawl", "tavily", "exa"]


def _find_config_path() -> Path | None:
    """Locate config file. Check env var first, then default location."""
    env_path = os.getenv("PAPER2WIKI_CONFIG", "").strip()
    if env_path:
        p = Path(env_path)
        return p if p.exists() else None

    default = Path.home() / ".paper2wiki" / "config.yaml"
    return default if default.exists() else None


def load_config() -> dict[str, Any]:
    """Load config.yaml. Returns empty dict if not found.

    """
    path = _find_config_path()
    if path is None:
        return {}

    try:
        import yaml

        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        return raw.get("web", {})
    except ImportError:
        logger.warning("PyYAML not installed — config.yaml ignored. pip install pyyaml")
        return {}
    except Exception as e:
        logger.warning("Failed to load config from %s: %s", path, e)
        return {}


class ProviderRegistry:
    """Registry of web providers with config-driven routing.

    Usage:
        registry = ProviderRegistry()
        provider = registry.get_search_provider()
        results = provider.search("transformer architecture", limit=5)
    """

    def __init__(self) -> None:
        # Lazy import to avoid circular deps — providers import from types,
        # registry imports providers.
        from src.tools.web_tools.providers import (
            ExaProvider,
            FirecrawlProvider,
            TavilyProvider,
        )

        # Map name → instance. Order doesn't matter here;
        # DEFAULT_PRIORITY controls the walk order.
        self._providers: dict[str, SearchProvider] = {
            "firecrawl": FirecrawlProvider(),
            "tavily": TavilyProvider(),
            "exa": ExaProvider(),
        }
        self._config: dict[str, Any] | None = None  # lazy loaded

    @property
    def config(self) -> dict[str, Any]:
        """Lazy-load config on first access."""
        if self._config is None:
            self._config = load_config()
        return self._config

    def _get_provider_by_name(self, name: str) -> SearchProvider | None:
        """Look up a provider by name. Returns None if unknown."""
        return self._providers.get(name.lower().strip())

    def _priority_walk(self, capability: str) -> SearchProvider | None:
        """Walk DEFAULT_PRIORITY, return first provider that is available
        AND supports the requested capability.

        """
        for name in DEFAULT_PRIORITY:
            provider = self._providers.get(name)
            if provider is None:
                continue

            # Check capability flag
            if capability == "search" and not provider.supports_search:
                continue
            if capability == "extract" and not provider.supports_extract:
                continue

            # Check credentials
            if provider.is_available():
                logger.debug("Priority walk selected: %s (for %s)", name, capability)
                return provider

        return None

    def _resolve(self, capability: str) -> SearchProvider | None:
        """Three-level fallback resolution.

          1. web.<capability>_backend config override
          2. web.backend shared config
          3. priority walk (first available)
        """
        # Level 1: per-capability override from config
        specific_name = self.config.get(f"{capability}_backend", "").strip()
        if specific_name:
            provider = self._get_provider_by_name(specific_name)
            if provider and provider.is_available():
                logger.debug("Config override: %s_backend = %s", capability, specific_name)
                return provider
            logger.warning(
                "Config %s_backend=%s but provider unavailable, falling through",
                capability, specific_name,
            )

        # Level 2: shared backend from config
        shared_name = self.config.get("backend", "").strip()
        if shared_name:
            provider = self._get_provider_by_name(shared_name)
            if provider and provider.is_available():
                logger.debug("Config shared backend = %s", shared_name)
                return provider

        # Level 3: priority walk
        return self._priority_walk(capability)

    def get_search_provider(self) -> SearchProvider | None:
        """Return the best available search provider, or None."""
        return self._resolve("search")

    def get_extract_provider(self) -> SearchProvider | None:
        """Return the best available extract provider, or None."""
        return self._resolve("extract")

    def list_available(self) -> list[str]:
        """Return names of all providers that currently have credentials."""
        return [
            name for name, provider in self._providers.items()
            if provider.is_available()
        ]


# Module-level singleton — import and use directly
registry = ProviderRegistry()
