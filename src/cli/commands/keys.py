"""``any2wiki keys`` — see and set API keys, without opening `.env` by hand.

Keys live in `.env`; settings live in `config.yaml`. Keeping them in separate commands
makes that split structural rather than a convention: `config set` can then *refuse* a
secret outright instead of guessing from the variable's name whether it is one.

Nothing here ever prints a raw key. `list` masks, `set` reads with the terminal echo off.

Commands:
    list  — which keys are set, masked.
    set   — prompt for one and write it to `.env`.
"""

from __future__ import annotations

import os
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from src.cli._env import _PROVIDER_KEY_ENV

app = typer.Typer(help="Inspect and set API keys (written to .env).")
console = Console()

#: Keys the app needs that are not tied to a model provider. Grouped so `list` shows
#: everything one place, rather than only the provider half.
_OTHER_KEYS: dict[str, str] = {
    "LANGSMITH_API_KEY": "tracing — needed by Loop 4 (trace analysis)",
    "DAYTONA_API_KEY": "the Marp slide sandbox (skip with --eval-mode)",
    "FIRECRAWL_API_KEY": "web search / extract",
    "TAVILY_API_KEY": "web search / extract",
    "EXA_API_KEY": "web search / extract",
    "SLACK_BOT_TOKEN": "the Slack front-end (`serve` only)",
    "SLACK_APP_TOKEN": "the Slack front-end (`serve` only)",
    "SLACK_CHANNEL_ID": "the Slack front-end (`serve` only)",
}


def mask(value: str) -> str:
    """Show enough of a key to recognise it, never enough to use it.

    The length guard matters: ``first6 + last4`` on a 12-character secret would reveal 10
    of its 12 characters, so anything short renders as nothing at all.
    """
    return f"{value[:6]}…{value[-4:]}" if len(value) > 14 else "…"


@app.command("list")
def list_keys() -> None:
    """Show which keys are set, masked — and which the configured model needs.

    Reads ``os.environ``, not the file: a key exported in your shell is what a run will
    actually use, whether or not it appears in `.env`.
    """
    from src.cli._env import _required_model_key

    needed = _required_model_key()

    table = Table(title="API keys", header_style="bold")
    table.add_column("Variable", style="bold")
    table.add_column("Value")
    table.add_column("Used for")

    provider_rows = sorted({v for v in _PROVIDER_KEY_ENV.values()})
    for name in provider_rows + list(_OTHER_KEYS):
        value = os.environ.get(name, "").strip()
        shown = mask(value) if value else "[dim]—[/]"
        purpose = _OTHER_KEYS.get(name, "a model provider")
        if name == needed:
            purpose = f"[yellow]required by your configured model[/] · {purpose}"
        elif not value:
            shown = "[dim]not set[/]"
        table.add_row(name, shown, purpose)

    console.print(table)
    if needed and not os.environ.get(needed):
        console.print(
            f"\n[yellow]{needed} is not set[/] — your configured model needs it: "
            f"[bold]any2wiki keys set {needed}[/]"
        )


@app.command("set")
def set_key_cmd(
    name: Annotated[str, typer.Argument(help="Variable name, e.g. OPENAI_API_KEY.")],
    value: Annotated[str | None, typer.Argument(
        help="The key. Omit it — you will be prompted, which keeps it out of shell history.",
    )] = None,
) -> None:
    """Write one key to `.env`, prompting for the value with echo off."""
    from dotenv import set_key

    from src.paths import env_path

    name = name.upper()
    if value is None:
        value = typer.prompt(name, hide_input=True).strip()
    if not value:
        console.print("[yellow]Nothing entered — no change.[/]")
        raise typer.Exit(1)

    path = env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create it already restricted. Letting set_key create it would leave a moment where
    # a umask-default (often 644, world-readable) file already holds the secret.
    if not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))

    set_key(str(path), name, value, quote_mode="never")
    path.chmod(0o600)  # also tightens a pre-existing world-readable .env

    console.print(f"Set [bold]{name}[/] = {mask(value)} in {path}")
