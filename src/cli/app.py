"""The entry point — where every command is registered.

Running ``any2wiki <command>`` (or ``python -m src.cli.app <command>``) starts here.
This file only connects the commands to their names; each one is written in ``commands/``.

Commands:
    repl          interactive chat session.
    chat MSG      run one message and exit.
    serve         listen on a Slack channel, run the agent per message (Loop 3).
    fetch [NAME]  pull raw source data into connectors/. No LLM.
    sessions ...  browse, search, resume or prune past sessions.
    config show   print the settings actually in effect.
    keys ...      see which API keys are set, and set them
    setup         first-run wizard: provider, model, per-task models, key

Two ordering rules this file exists to enforce:

1. ``.env`` is loaded in the root callback, so credentials exist before any command runs.
2. Flags that change which tools get registered (``--ingest-mode``, ``--wiki-path``) are
   applied inside each command body, which then imports the agent *lazily*. Importing it
   at the top of a module would lock in the tool list before the flags were read.

Functions:
    _main() — the root callback; loads .env before any command.
    main()  — module entry point for ``python -m src.cli.app``.
"""

from __future__ import annotations

import typer

from src.env import load_env
from src.cli.commands import chat as chat_cmd
from src.cli.commands import config as config_cmd
from src.cli.commands import fetch as fetch_cmd
from src.cli.commands import keys as keys_cmd
from src.cli.commands import setup as setup_cmd
from src.cli.commands import sessions as sessions_cmd
from src.cli.commands import slack as slack_cmd

# TODO: enable auto-completion. `sessions resume`/`rename` already pass
# autocompletion=_complete_session_ref, and `--install-completion` is the documented
# way to enable it — but add_completion=False removes that flag, so neither can fire.
# Flip this to True, or drop the completers.
app = typer.Typer(
    name="any2wiki",
    help="Any2Wiki — a self-improving LLM knowledge base.",
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
app.command("fetch")(fetch_cmd.fetch)
app.command("setup")(setup_cmd.setup)  # first-run wizard # register the connector fetch
app.add_typer(sessions_cmd.app, name="sessions", help="Browse/search/resume/prune saved sessions.")
app.add_typer(keys_cmd.app, name="keys", help="Inspect and set API keys (written to .env).")
app.add_typer(config_cmd.app, name="config", help="Inspect ingest mode, wiki path, and available web providers.")


def main() -> None:
    """Module entry point (``python -m src.cli.app``)."""
    app(prog_name="any2wiki")


if __name__ == "__main__":
    main()
