"""Connector registry — phase 1 of the fetch/synthesise split.

Adding a source: write the class, add one line here. Mirrors the shape of
``CHECKS`` in ``src/middleware/checks/__init__.py``.
"""

from __future__ import annotations

from src.connectors.base import Connector, FetchResult, Item, run_fetch
from src.connectors.git_repo import GitRepoConnector

REGISTRY: dict[str, Connector] = {
    "git-repo": GitRepoConnector(),
}


def get_connector(name: str) -> Connector | None:
    """The connector registered under ``name``, or None."""
    return REGISTRY.get(name)


__all__ = ["REGISTRY", "get_connector", "run_fetch", "Item", "Connector", "FetchResult"]
