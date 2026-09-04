"""``paper2wiki config show`` — print the settings actually in effect.

Answers "what will this run actually do?" without starting the agent: which model each
task resolved to, which ingest mode, which wiki directory, and which web-search providers
have keys. Reads config the same way the app does, so what it prints is what the app will
use — including any flags you pass here, which is how you preview an override before
committing to it.

Functions:
    show(...) — print the resolved ingest mode, wiki path, and available web providers.
"""

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
    model: Annotated[str | None, typer.Option("--model", "-m", help="Preview this base model.")] = None,
) -> None:
    """Show the resolved model, ingest mode, wiki path, and available web providers."""
    apply_env(ingest_mode, wiki_path, model)

    from src.ingest_mode import get_ingest_mode

    table = Table(title="Effective configuration", header_style="bold", show_header=False)
    table.add_column("Setting", style="bold")
    table.add_column("Value")

    table.add_row("Ingest mode", get_ingest_mode())
    table.add_row("Wiki path", os.environ.get("WIKI_PATH", "./wiki (default)"))

    # Per-task, because any task can be pointed at a different model.
    try:
        from src.llm_roles import VALID_ROLES, get_model_spec

        for role in sorted(VALID_ROLES):
            spec = get_model_spec(role)
            suffix = f"  [dim]via {spec.base_url}[/]" if spec.base_url else ""
            table.add_row(f"Model · {role}", f"{spec.model}{suffix}")
    except Exception as exc:  # pragma: no cover - defensive
        table.add_row("Model", f"[dim]unavailable ({exc})[/]")

    try:
        from src.tools.web_tools.registry import registry

        available = registry.list_available()
        table.add_row("Web providers", ", ".join(available) if available else "[red]none[/]")
    except Exception as exc:  # pragma: no cover - defensive: provider import is optional
        table.add_row("Web providers", f"[dim]unavailable ({exc})[/]")

    console.print(table)
