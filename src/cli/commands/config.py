"""``paper2wiki config show`` — print the settings actually in effect.

Answers "what will this run actually do?" without starting the agent, and without
spending anything: which model each task resolved to, **which provider and endpoint it
will be sent to**, the ingest mode, the wiki directory, and which web-search providers
have keys.

Showing the provider and endpoint is the point, not decoration. A model name alone cannot
tell you whether `claude-sonnet-4-6` goes to Anthropic directly, through OpenRouter, or to
some unexpected endpoint — and a mismatch between name and provider is what produces
``404 not_found_error: model: openai:gpt-4o``.

Reads config exactly the way the app does, so what it prints is what a real run will use —
including any flags you pass here, which is how you preview an override before committing
to it.

Functions:
    show(...)          — print the resolved settings and the per-task model table.
    _routing(spec)     — where one task's request will actually be sent.
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


def _routing(spec) -> tuple[str, str]:
    """Return ``(provider, endpoint)`` as they will actually be resolved.

    ``spec.provider`` is often None, which does **not** mean "no provider" — it means
    nobody pinned one, so ``init_chat_model`` works it out from the model string. This
    reproduces that so the table shows the real destination rather than a blank.
    """
    from src.cli._env import _infer_provider
    from src.llm_roles import _explicit_provider_prefix

    if spec.provider:
        provider = spec.provider
    elif prefix := _explicit_provider_prefix(spec.model):
        provider = f"{prefix} [dim](from model)[/]"
    elif inferred := _infer_provider(spec.model):
        provider = f"{inferred} [dim](inferred)[/]"
    else:
        provider = "[red]unknown[/]"

    endpoint = spec.base_url or "[dim]provider default[/]"
    return provider, endpoint


@app.command("show")
def show(
    ingest_mode: Annotated[IngestMode | None, typer.Option("--ingest-mode", help="Preview this ingest mode.")] = None,
    wiki_path: Annotated[str | None, typer.Option("--wiki-path", help="Preview this wiki path.")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Preview this base model.")] = None,
) -> None:
    """Show the resolved models (with provider + endpoint), ingest mode, and wiki path."""
    apply_env(ingest_mode, wiki_path, model)

    from src.ingest_mode import get_ingest_mode

    settings = Table(title="Effective configuration", header_style="bold", show_header=False)
    settings.add_column("Setting", style="bold")
    settings.add_column("Value")

    settings.add_row("Ingest mode", get_ingest_mode())
    settings.add_row("Wiki path", os.environ.get("WIKI_PATH", "./wiki (default)"))

    try:
        from src.tools.web_tools.registry import registry

        available = registry.list_available()
        settings.add_row("Web providers", ", ".join(available) if available else "[red]none[/]")
    except Exception as exc:  # pragma: no cover - defensive: provider import is optional
        settings.add_row("Web providers", f"[dim]unavailable ({exc})[/]")

    console.print(settings)

    # Per task, because any task can be pointed at a different model, provider or endpoint.
    models = Table(title="Models", header_style="bold")
    models.add_column("Task", style="bold")
    models.add_column("Model")
    models.add_column("Provider")
    models.add_column("Endpoint")

    try:
        from src.llm_roles import VALID_ROLES, get_model_spec

        for role in sorted(VALID_ROLES):
            spec = get_model_spec(role)
            provider, endpoint = _routing(spec)
            models.add_row(role, spec.model, provider, endpoint)
    except Exception as exc:  # pragma: no cover - defensive
        models.add_row("[dim]unavailable[/]", f"[dim]{exc}[/]", "", "")

    console.print(models)
