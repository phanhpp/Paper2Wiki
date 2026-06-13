"""Environment plumbing shared by CLI commands.

``apply_env`` must run *before* the agent/tools import graph is touched, because
``src/tools/__init__.py`` builds ``all_tools`` at import time from the resolved ingest mode
(``src/ingest_mode.py:get_ingest_mode``). Command bodies therefore call ``apply_env`` first,
then import ``create_supervisor`` lazily.
"""

from __future__ import annotations

import os
from enum import Enum

import typer


class IngestMode(str, Enum):
    fast = "fast"
    quality = "quality"


def apply_env(ingest_mode: IngestMode | None, wiki_path: str | None) -> None:
    """Translate CLI flags into the env vars the rest of the app already reads."""
    if ingest_mode is not None:
        os.environ["PAPER2WIKI_INGEST_MODE"] = ingest_mode.value
    if wiki_path:
        os.environ["WIKI_PATH"] = wiki_path


def require_keys(eval_mode: bool) -> None:
    """Fail fast with a readable message when required credentials are missing.

    ``ANTHROPIC_API_KEY`` is always required. ``DAYTONA_API_KEY`` is only needed when the
    Daytona-sandboxed marp subagent is built (i.e. not in eval mode).
    """
    missing = []
    if not os.environ.get("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    if not eval_mode and not os.environ.get("DAYTONA_API_KEY"):
        missing.append("DAYTONA_API_KEY (or pass --eval-mode to skip the Daytona sandbox)")

    if missing:
        typer.secho(
            "Missing required environment variable(s):\n  - " + "\n  - ".join(missing)
            + "\nSet them in your .env (see .env.example).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
