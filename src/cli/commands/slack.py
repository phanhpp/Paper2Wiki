"""``paper2wiki serve`` — run the agent against a Slack channel (Loop 3).

Thin CLI wrapper, mirroring ``commands/chat.py``: options are declared here next
to Typer, the listener itself lives in ``src/slack/app.py``.
"""

from __future__ import annotations

from typing import Annotated

import typer

from src.cli._env import IngestMode, apply_env, require_keys, setup_logging


def serve(
    channel_id: Annotated[str | None, typer.Option("--channel", help="Slack channel id (else $SLACK_CHANNEL_ID).")] = None,
    ingest_mode: Annotated[IngestMode | None, typer.Option("--ingest-mode", help="Override ingest mode.")] = None,
    wiki_path: Annotated[str | None, typer.Option("--wiki-path", help="Override the wiki directory.")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Auto-approve all HITL prompts (no buttons).")] = False,
    eval_mode: Annotated[bool, typer.Option("--eval-mode", help="Skip the Daytona sandbox.")] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Show diagnostic output.")] = False,
) -> None:
    """Listen to a Slack channel and run the agent on each message.

    Opens a Socket Mode websocket — no public URL needed — and answers in the
    thread the message came from. Runs until interrupted.
    """
    setup_logging(debug)
    apply_env(ingest_mode, wiki_path)
    require_keys(eval_mode, slack=True)

    # Lazy import: must follow apply_env() so tool registration sees the ingest mode.
    from src.slack.app import serve as _serve

    try:
        _serve(
            channel_id=channel_id,
            eval_mode=eval_mode,
            auto_approve=yes,
            debug=debug,
        )
    except KeyboardInterrupt:
        typer.secho("\nStopped.", fg=typer.colors.YELLOW)
    except RuntimeError as exc:
        # Raised by check_credentials with an actionable message — show just that,
        # not a traceback.
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
