"""Shared ingest-mode resolution for production and eval code."""

from __future__ import annotations

import os

VALID_INGEST_MODES = {"quality", "fast"}


def get_ingest_mode() -> str:
    """Return active ingest mode: env var > config file > default ``fast``."""
    mode = os.environ.get("PAPER2WIKI_INGEST_MODE", "").strip().lower()
    if mode not in VALID_INGEST_MODES:
        try:
            from src.tools.web_tools.registry import load_config_file

            mode = load_config_file().get("ingest", {}).get("mode", "fast").strip().lower()
        except Exception:
            mode = "fast"
    return mode if mode in VALID_INGEST_MODES else "fast"
