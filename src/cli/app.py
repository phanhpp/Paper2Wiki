"""Paper2Wiki CLI entry point (``paper2wiki``).

Top-level commands:
  repl          interactive chat session
  chat MSG      one-shot message
  sessions ...  browse/search/resume/prune saved sessions
  config show   inspect effective configuration

``.env`` is loaded in the root callback so credentials are available before any command runs.
Per-command flags that affect tool registration (``--ingest-mode``/``--wiki-path``) are applied
inside the command bodies, which import the agent lazily so the env is set first.
"""

from __future__ import annotations

import typer

from src.env import load_env
from src.cli.commands import chat as chat_cmd
from src.cli.commands import config as config_cmd
from src.cli.commands import sessions as sessions_cmd
from src.cli.commands import slack as slack_cmd

# TODO: add auto-completion support
app = typer.Typer(
    name="paper2wiki",
    help="Paper2Wiki — a self-improving LLM knowledge base.",
    no_args_is_help=True, # when no arguments are provided, show the help message
    add_completion=False,
    # On by default: suggesting mistyped command names
)


@app.callback() # run before any command executes
def _main() -> None:
    """Load environment variables before any command executes."""
    load_env()
    


app.command("repl")(chat_cmd.repl) # register the repl command
app.command("chat")(chat_cmd.chat) # register the chat command
app.command("serve")(slack_cmd.serve) # register the Slack listener
app.add_typer(sessions_cmd.app, name="sessions", help="Browse/search/resume/prune saved sessions.")
app.add_typer(config_cmd.app, name="config", help="Inspect ingest mode, wiki path, and available web providers.")


def main() -> None:
    """Module entry point (``python -m src.cli.app``)."""
    app(prog_name="paper2wiki")


if __name__ == "__main__":
    main()
