"""``any2wiki setup`` — first-run wizard.

Exists because the per-task `auxiliary` block is the setting that saves the most money
and the one nobody finds: it appears only as commented-out examples in
`config.example.yaml`. A wizard is where that becomes a question rather than a surprise.

Writes `config.yaml` through `src.paths.config_path()` and any key through
`keys set`, so the wizard cannot disagree with the commands about where things live.

Functions:
    setup(...)          — the wizard; `--yes` runs it non-interactively.
    _suggested_models(provider) — a couple of sensible ids per provider.
"""

from __future__ import annotations

from typing import Annotated

import os

import typer
from rich.console import Console

from src.cli._env import _PROVIDER_KEY_ENV

app_console = Console()

#: A starting point per provider. Not exhaustive, and not validated against the provider —
#: model ids change faster than this file does, so free text is always allowed.
_SUGGESTED: dict[str, list[str]] = {
    "anthropic": ["claude-sonnet-4-6", "claude-haiku-4-5-20251001"],
    "openai": ["openai:gpt-4o", "openai:gpt-4o-mini"],
    "google_genai": ["google_genai:gemini-2.0-flash"],
    "groq": ["groq:llama-3.3-70b-versatile"],
    "deepseek": ["deepseek:deepseek-chat"],
}

#: What the five non-supervisor tasks drop to when the user accepts the cheap default.
_CHEAP: dict[str, str] = {
    "anthropic": "claude-haiku-4-5-20251001",
    "openai": "openai:gpt-4o-mini",
    "google_genai": "google_genai:gemini-2.0-flash",
}

_SIDE_TASKS = ("subagent", "title", "summarize", "judge", "web_summarize")


def _suggested_models(provider: str) -> list[str]:
    return _SUGGESTED.get(provider, [])


def setup(
    provider: Annotated[str | None, typer.Option("--provider", help="Model provider, e.g. openai.")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Base model id.")] = None,
    cheap_aux: Annotated[bool | None, typer.Option(
        "--cheap-aux/--no-cheap-aux",
        help="Use a cheaper model for the 5 background tasks.",
    )] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Non-interactive; accept defaults.")] = False,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing config.yaml.")] = False,
) -> None:
    """Create `config.yaml`, and offer to store the provider's API key.

    `--yes` makes it non-interactive for scripted installs and for testing the wizard
    itself. A missing API key is a warning, not a failure: writing config now and adding
    the key later is a normal flow, and `require_keys()` already refuses at run time
    naming the exact variable.
    """
    import yaml

    from src.paths import config_path, ensure_user_root

    console = app_console
    path = config_path()

    if path.exists() and not force:
        console.print(
            f"[yellow]{path} already exists.[/] "
            "Re-run with [bold]--force[/] to overwrite, or edit it with "
            "[bold]any2wiki config set[/]."
        )
        raise typer.Exit(1)

    # 1 · provider
    known = sorted(_PROVIDER_KEY_ENV)
    if provider is None:
        if yes:
            provider = "anthropic"
        else:
            console.print("\n[bold]Providers[/] (✓ = key already set)")
            for name in known:
                mark = "✓" if os.environ.get(_PROVIDER_KEY_ENV[name]) else " "
                console.print(f"  {mark} {name}")
            provider = typer.prompt("\nProvider", default="anthropic").strip()

    if provider not in _PROVIDER_KEY_ENV:
        console.print(
            f"[yellow]Unknown provider {provider!r}.[/] Continuing — you may need to set "
            "`base_url` and `api_key` by hand for an OpenAI-compatible endpoint."
        )

    # 2 · model
    if model is None:
        options = _suggested_models(provider)
        default = options[0] if options else ""
        if yes:
            model = default
        else:
            if options:
                console.print(f"\n[dim]Suggestions: {', '.join(options)}[/]")
            model = typer.prompt("Base model", default=default).strip()
    if not model:
        console.print("[red]No model chosen.[/]")
        raise typer.Exit(1)

    # 3 · cheaper models for the side tasks — the step the wizard exists for
    cheap_model = _CHEAP.get(provider)
    if cheap_aux is None:
        cheap_aux = bool(cheap_model) and (
            True if yes else typer.confirm(
                f"\nUse {cheap_model} for the 5 background tasks "
                "(titles, summaries, judging)?",
                default=True,
            )
        )

    config: dict = {"model": {"default": model}}
    if cheap_aux and cheap_model:
        config["auxiliary"] = {task: {"model": cheap_model} for task in _SIDE_TASKS}

    ensure_user_root()
    path.write_text(yaml.safe_dump(config, sort_keys=False, default_flow_style=False))
    console.print(f"\nWrote [bold]{path}[/]")

    # 4 · the key — a warning, never a failure
    key_var = _PROVIDER_KEY_ENV.get(provider)
    if key_var and not os.environ.get(key_var):
        if yes:
            console.print(
                f"[yellow]{key_var} is not set[/] — set it before running: "
                f"[bold]any2wiki keys set {key_var}[/]"
            )
        elif typer.confirm(f"\n{key_var} is not set. Enter it now?", default=True):
            from src.cli.commands.keys import set_key_cmd

            set_key_cmd(key_var, None)

    console.print(
        "\nCheck it with [bold]any2wiki config show[/], or test every task with\n"
        "  [dim]uv run --env-file .env python scripts/probe_roles.py[/]"
    )
