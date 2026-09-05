"""``any2wiki serve`` — answer Slack messages instead of terminal input (Loop 3).

Same agent, same wiki, same databases as the REPL — only the front-end differs. A
message starts a turn, a threaded reply continues that conversation, and approvals
become buttons.

It connects *outward* over a websocket (Socket Mode), so no public URL or webhook is
needed. The trade-off is honest: it only answers while this command is running.

The listener itself is in ``src/slack/app.py``; this file only declares the flags and
turns two known failures into readable messages instead of tracebacks — Ctrl-C, and bad
or missing credentials.

Functions:
    serve(...) — connect to Slack and run until interrupted.
"""

from __future__ import annotations

from typing import Annotated

import typer

from src.cli._env import IngestMode, apply_env, require_keys, setup_logging


def serve(
    channel_id: Annotated[str | None, typer.Option("--channel", help="Slack channel id (else $SLACK_CHANNEL_ID).")] = None,
    ingest_mode: Annotated[IngestMode | None, typer.Option("--ingest-mode", help="Override ingest mode.")] = None,
    wiki_path: Annotated[str | None, typer.Option("--wiki-path", help="Override the wiki directory.")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Override the base model, e.g. openai:gpt-4o.")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Auto-approve all HITL prompts (no buttons).")] = False,
    eval_mode: Annotated[bool, typer.Option("--eval-mode", help="Skip the Daytona sandbox.")] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Show diagnostic output.")] = False,
) -> None:
    """Listen to a Slack channel and run the agent on each message.

    Opens a Socket Mode websocket — no public URL needed — and answers in the
    thread the message came from. Runs until interrupted.
    """
    setup_logging(debug)
    apply_env(ingest_mode, wiki_path, model)
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
