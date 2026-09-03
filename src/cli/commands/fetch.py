"""``paper2wiki fetch`` — phase 1 of ingest: pull raw source data, no LLM.

Thin CLI wrapper, mirroring ``commands/slack.py``: options live here next to
Typer, the work lives in ``src/connectors/``.
"""

from __future__ import annotations

from typing import Annotated

import typer

from src.cli._env import IngestMode, apply_env, setup_logging


def fetch(
    connector: Annotated[str | None, typer.Argument(help="Connector to run; omit to run all configured.")] = None,
    ingest_mode: Annotated[IngestMode | None, typer.Option("--ingest-mode", help="Override ingest mode.")] = None,
    wiki_path: Annotated[str | None, typer.Option("--wiki-path", help="Override the wiki directory.")] = None,
    debug: Annotated[bool, typer.Option("--debug", help="Show diagnostic output.")] = False,
) -> None:
    """Fetch raw data from a connector into ``connectors/<name>/``.

    Deterministic and LLM-free: it hits the source, writes the raw responses, and
    records each item in a manifest. Nothing is synthesised into the wiki here —
    that is a separate agent run, which can be repeated for free because the raw
    data is already local.
    """
    setup_logging(debug)
    apply_env(ingest_mode, wiki_path)
    # No require_keys: connectors carry their own credentials (or none at all),
    # and git-repo needs nothing.

    from src.connectors import REGISTRY, run_fetch
    from src.connectors.base import CONNECTORS_DIR
    from src.tools.web_tools.registry import load_config_file

    configured = (load_config_file().get("connectors") or {})

    if connector and connector not in REGISTRY:
        typer.secho(
            f"Unknown connector {connector!r}. Available: {', '.join(sorted(REGISTRY))}",
            fg=typer.colors.RED, err=True,
        )
        raise typer.Exit(1)

    names = [connector] if connector else sorted(REGISTRY)
    any_ran = False

    for name in names:
        cfg = configured.get(name) or {}
        if not cfg.get("enabled", bool(cfg)):
            typer.secho(f"{name}: not configured — skipping (see config.yaml)", fg=typer.colors.YELLOW)
            continue

        any_ran = True
        typer.secho(f"{name}: fetching…", fg=typer.colors.CYAN)
        result = run_fetch(REGISTRY[name], cfg)

        typer.secho(
            f"  {result.new} new · {result.unchanged} unchanged · {result.deleted} gone",
            fg=typer.colors.GREEN,
        )
        for warning in result.warnings:
            typer.secho(f"  warning: {warning}", fg=typer.colors.YELLOW, err=True)

    if any_ran:
        typer.secho(f"\nRaw data in {CONNECTORS_DIR}", fg=typer.colors.BRIGHT_BLACK)
