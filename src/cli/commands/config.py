"""``any2wiki config show`` — print the settings actually in effect.

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
    path()             — print which config file is in use.
    _routing(spec)     — where one task's request will actually be sent.
    _source(role)      — which precedence level supplied this task's model.
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


def _source(role: str) -> str:
    """Which of the five precedence levels supplied this task's model.

    Editing `auxiliary.<task>.model` and seeing `config show` print the old value looks
    like a broken command — the cause is an env var silently winning over the file. This
    column names the winner, so the symptom and its cause appear together.
    """
    if os.environ.get(f"ANY2WIKI_MODEL_{role.upper()}", "").strip():
        return "[yellow]env[/]"

    from src.llm_roles import _aux_block

    if (_aux_block(role) or {}).get("model"):
        return f"auxiliary.{role}.model"
    if os.environ.get("ANY2WIKI_MODEL", "").strip():
        return "[yellow]env[/]"

    from src.llm_roles import _model_block

    return "model.default" if (_model_block() or {}).get("default") else "[dim]built-in[/]"


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
        provider = prefix
    elif inferred := _infer_provider(spec.model):
        provider = f"[dim]{inferred}[/]"  # inferred from the model name
    else:
        provider = "[red]unknown[/]"

    endpoint = spec.base_url or "[dim]—[/]"   # dash = the provider's own API
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

    from src.tools.web_tools.registry import _find_config_path

    found = _find_config_path()
    settings.add_row(
        "Config file",
        str(found) if found else "[yellow]none found — run: any2wiki setup[/]",
    )
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
    models.add_column("From")

    try:
        from src.llm_roles import VALID_ROLES, get_model_spec

        for role in sorted(VALID_ROLES):
            spec = get_model_spec(role)
            provider, endpoint = _routing(spec)
            models.add_row(role, spec.model, provider, endpoint, _source(role))
    except Exception as exc:  # pragma: no cover - defensive
        models.add_row("[dim]unavailable[/]", f"[dim]{exc}[/]", "", "")

    console.print(models)

    # The column can only fit "env"; name the variables underneath, where a narrow
    # terminal cannot truncate them away.
    shadowing = sorted({
        var
        for role in VALID_ROLES
        for var in (f"ANY2WIKI_MODEL_{role.upper()}", "ANY2WIKI_MODEL")
        if os.environ.get(var, "").strip()
    })
    if shadowing:
        console.print(
            "\n[yellow]Overridden by environment variables:[/] " + ", ".join(shadowing)
            + "\n[dim]These outrank config.yaml — unset them to use the file.[/]"
        )


@app.command("path")
def path() -> None:
    """Print the config file in use, and where `setup` would write one.

    `config show` reads whatever this resolves to. When a change appears to do nothing,
    this is the first thing to check — the repo copy, `~/.any2wiki/` and "no file at all"
    otherwise render identically.
    """
    from src.paths import config_path
    from src.tools.web_tools.registry import _find_config_path

    found = _find_config_path()
    if found:
        # Plain print, not console.print: Rich wraps at the terminal width, which would
        # split a long path across lines and break `$(any2wiki config path)`.
        print(found)
        return

    console.print(f"[yellow]No config file found.[/] `setup` would create: {config_path()}")
    raise typer.Exit(1)


#: Top-level blocks a value may be set under. Anything else is a typo, and silently
#: creating it would produce a key nothing ever reads.
_KNOWN_BLOCKS = frozenset({
    "model", "auxiliary", "web", "ingest", "verification", "connectors",
})

#: A value under any of these names belongs in `.env`, never in config.yaml.
_SECRET_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")


def _coerce(raw: str):
    """Turn a CLI string into the type YAML would have held.

    `timeout: "60"` and `timeout: 60` are different values to a client library, and the
    shell only ever hands us strings.
    """
    low = raw.strip().lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none", "~"):
        return None
    for cast in (int, float):
        try:
            return cast(raw)
        except ValueError:
            pass
    return raw


def _validate(parts: list[str], key: str) -> None:
    """Reject a path that would write a key nothing reads. Raises typer.Exit."""
    from src.llm_roles import VALID_ROLES

    # Check the *last segment*, not the whole dotted key: "model.api_key".upper() ends
    # with ".API_KEY", never "_API_KEY", so matching the full string misses it entirely.
    leaf = parts[-1].upper()
    if leaf == "API_KEY" or any(leaf.endswith(sfx) for sfx in _SECRET_SUFFIXES):
        console.print(
            f"[red]{key} looks like a secret.[/] Secrets go in .env, not config.yaml:\n"
            f"  [bold]any2wiki keys set {parts[-1].upper()}[/]"
        )
        raise typer.Exit(1)

    if parts[0] not in _KNOWN_BLOCKS:
        console.print(
            f"[red]Unknown config block {parts[0]!r}.[/] "
            f"Expected one of: {', '.join(sorted(_KNOWN_BLOCKS))}"
        )
        raise typer.Exit(1)

    if parts[0] == "auxiliary" and len(parts) > 1 and parts[1] not in VALID_ROLES:
        console.print(
            f"[red]Unknown task {parts[1]!r}.[/] "
            f"Expected one of: {', '.join(sorted(VALID_ROLES))}"
        )
        raise typer.Exit(1)


@app.command("set")
def set_value(
    key: Annotated[str, typer.Argument(help="Dotted path, e.g. auxiliary.judge.model")],
    value: Annotated[str, typer.Argument(help="The value. true/false and numbers are converted.")],
) -> None:
    """Set one value in config.yaml, then show what a run would now resolve to.

    Comments in the file are not preserved — `config.example.yaml` is the documented
    reference; this file is plain settings.
    """
    import yaml

    from src.paths import config_path, ensure_user_root

    parts = [p for p in key.split(".") if p]
    if not parts:
        console.print("[red]Empty key.[/]")
        raise typer.Exit(1)
    _validate(parts, key)

    ensure_user_root()
    path = config_path()
    data = yaml.safe_load(path.read_text()) if path.exists() else {}
    if not isinstance(data, dict):
        data = {}

    cursor = data
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = _coerce(value)

    path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
    console.print(f"Set [bold]{key}[/] in {path}\n")

    # Explicit Nones: calling a Typer command directly would otherwise pass its
    # `typer.Option(...)` defaults through as values.
    show(ingest_mode=None, wiki_path=None, model=None)
