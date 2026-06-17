"""Inspect the effective runtime configuration."""

from __future__ import annotations

import os
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from src.cli._env import IngestMode, apply_env

app = typer.Typer(help="Inspect effective configuration.")
console = Console()


@app.command("show")
def show(
    ingest_mode: Annotated[IngestMode | None, typer.Option("--ingest-mode", help="Preview this ingest mode.")] = None,
    wiki_path: Annotated[str | None, typer.Option("--wiki-path", help="Preview this wiki path.")] = None,
) -> None:
    """Show the resolved ingest mode, wiki path, and available web providers."""
    apply_env(ingest_mode, wiki_path)

    from src.ingest_mode import get_ingest_mode

    table = Table(title="Effective configuration", header_style="bold", show_header=False)
    table.add_column("Setting", style="bold")
    table.add_column("Value")

    table.add_row("Ingest mode", get_ingest_mode())
    table.add_row("Wiki path", os.environ.get("WIKI_PATH", "./wiki (default)"))

    try:
        from src.tools.web_tools.registry import registry

        available = registry.list_available()
        table.add_row("Web providers", ", ".join(available) if available else "[red]none[/]")
    except Exception as exc:  # pragma: no cover - defensive: provider import is optional
        table.add_row("Web providers", f"[dim]unavailable ({exc})[/]")

    console.print(table)
