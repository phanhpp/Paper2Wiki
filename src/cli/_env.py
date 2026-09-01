"""Environment plumbing shared by CLI commands.

``apply_env`` must run *before* the agent/tools import graph is touched, because
``src/tools/__init__.py`` builds ``all_tools`` at import time from the resolved ingest mode
(``src/ingest_mode.py:get_ingest_mode``). Command bodies therefore call ``apply_env`` first,
then import ``create_supervisor`` lazily.
"""

from __future__ import annotations

import logging
import os
from enum import Enum

import typer


class IngestMode(str, Enum):
    fast = "fast"
    quality = "quality"


def setup_logging(debug: bool = False) -> None:
    """Configure console logging for the CLI.

    Default: only WARNING+ reaches the console, so startup stays quiet. ``--debug``
    drops the level to DEBUG and surfaces the agent/tool diagnostics (Daytona build,
    provider routing, session save, etc.). Third-party loggers are pinned to WARNING
    unless ``--debug`` so their chatter doesn't leak in.
    """
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,  # take effect even if some import already configured the root logger
    )
    if not debug:
        for noisy in ("httpx", "httpcore", "urllib3", "anthropic", "openai", "daytona"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def apply_env(ingest_mode: IngestMode | None, wiki_path: str | None) -> None:
    """Translate CLI flags into the env vars the rest of the app already reads."""
    if ingest_mode is not None:
        os.environ["PAPER2WIKI_INGEST_MODE"] = ingest_mode.value
    if wiki_path:
        os.environ["WIKI_PATH"] = wiki_path


def require_keys(eval_mode: bool, *, slack: bool = False) -> None:
    """Fail fast with a readable message when required credentials are missing.

    ``ANTHROPIC_API_KEY`` is always required. ``DAYTONA_API_KEY`` is only needed when the
    Daytona-sandboxed marp subagent is built (i.e. not in eval mode). The Slack
    credentials are only needed by ``serve`` — every other command works without
    them, since Slack is an optional second way in, not part of the core CLI.
    """
    missing = []
    if not os.environ.get("ANTHROPIC_API_KEY"):
        missing.append("ANTHROPIC_API_KEY")
    if not eval_mode and not os.environ.get("DAYTONA_API_KEY"):
        missing.append("DAYTONA_API_KEY (or pass --eval-mode to skip the Daytona sandbox)")
    if slack:
        # Workspace-scoped and per-user: each person creates their own Slack app.
        # docs/slack_setup.md walks through it — the two tokens come from two
        # different pages, which is the usual thing to get wrong.
        for var in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_CHANNEL_ID"):
            if not os.environ.get(var):
                missing.append(f"{var} (see docs/slack_setup.md)")

    if missing:
        typer.secho(
            "Missing required environment variable(s):\n  - " + "\n  - ".join(missing)
            + "\nSet them in your .env (see .env.example).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
