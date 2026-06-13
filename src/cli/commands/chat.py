"""Interactive REPL and one-shot chat commands.

The REPL builds the supervisor once (it spins up a thread-scoped Daytona sandbox) and reuses
it across turns by passing ``agent=`` into every ``run_turn_stream_async`` call. One-shot
``chat`` builds a supervisor per invocation.
"""

from __future__ import annotations

import typer
from langchain_core.utils.uuid import uuid7
from typing import Annotated
from src.cli._env import IngestMode, apply_env, require_keys
from src.cli._runtime import run_async

_REPL_HELP = """\
Commands:
  /new    start a fresh session (new thread + sandbox)
  /help   show this help
  /exit   quit (also Ctrl-D)
"""


def _new_thread_id() -> str:
    return str(uuid7())


def run_chat(
    message: str,
    *,
    thread_id: str | None,
    ingest_mode: IngestMode | None,
    wiki_path: str | None,
    yes: bool,
    eval_mode: bool,
    debug: bool,
) -> None:
    """One-shot: run a single message through a fresh supervisor and exit."""
    apply_env(ingest_mode, wiki_path)
    require_keys(eval_mode)

    # Lazy imports: must follow apply_env() so tool registration sees the ingest mode.
    from src.agents.agent import create_supervisor
    from src.agents.stream import run_turn_stream_async
    from src.cli.renderer import RichRenderer

    tid = thread_id or _new_thread_id()
    renderer = RichRenderer(auto_approve=yes, debug=debug)

    async def _run() -> None:
        agent = await create_supervisor(tid, eval_mode=eval_mode)
        await run_turn_stream_async(message, agent=agent, thread_id=tid, renderer=renderer)

    run_async(_run())


def run_repl(
    *,
    thread_id: str | None,
    ingest_mode: IngestMode | None,
    wiki_path: str | None,
    yes: bool,
    eval_mode: bool,
    debug: bool,
) -> None:
    """Interactive loop: build the supervisor once, stream each typed turn."""
    apply_env(ingest_mode, wiki_path)
    require_keys(eval_mode)

    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory

    from src.agents.agent import create_supervisor
    from src.agents.stream import run_turn_stream_async
    from src.cli.renderer import RichRenderer

    renderer = RichRenderer(auto_approve=yes, debug=debug)
    console = renderer.console
    current = {"tid": thread_id or _new_thread_id()}

    async def _loop() -> None:
        agent = await create_supervisor(current["tid"], eval_mode=eval_mode)
        session: PromptSession = PromptSession(history=InMemoryHistory())
        console.print(f"[bold green]Paper2Wiki[/] — session [dim]{current['tid']}[/]")
        console.print("Type a message, or [cyan]/help[/] for commands.\n")

        while True:
            try:
                user_in = (await session.prompt_async("you ❯ ")).strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_in:
                continue
            if user_in in ("/exit", "/quit"):
                break
            if user_in == "/help":
                console.print(_REPL_HELP)
                continue
            if user_in == "/new":
                current["tid"] = _new_thread_id()
                agent = await create_supervisor(current["tid"], eval_mode=eval_mode)
                console.print(f"[dim]New session {current['tid']}[/]\n")
                continue

            await run_turn_stream_async(
                user_in, agent=agent, thread_id=current["tid"], renderer=renderer
            )

        console.print("\n[dim]Goodbye.[/]")

    run_async(_loop())


# --- Typer command wrappers ------------------------------------------------

def chat(
    message: Annotated[str, typer.Argument(help="The message to send to the agent.")],
    thread_id: Annotated[str | None, typer.Option("--thread-id", "-t", help="Resume an existing thread.")] = None,
    ingest_mode: Annotated[IngestMode | None, typer.Option("--ingest-mode", help="Override ingest mode.")] = None,
    wiki_path: Annotated[str | None, typer.Option("--wiki-path", help="Override the wiki directory.")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Auto-approve all HITL prompts.")] = False,
    eval_mode: Annotated[bool, typer.Option("--eval-mode", help="Skip the Daytona sandbox.")] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Show diagnostic output.")] = False,
) -> None:
    """Run a single message and exit (non-interactive)."""
    run_chat(
        message,
        thread_id=thread_id,
        ingest_mode=ingest_mode,
        wiki_path=wiki_path,
        yes=yes,
        eval_mode=eval_mode,
        debug=debug,
    )


def repl(
    thread_id: Annotated[str | None, typer.Option("--thread-id", "-t", help="Resume an existing thread.")] = None,
    ingest_mode: Annotated[IngestMode | None, typer.Option("--ingest-mode", help="Override ingest mode.")] = None,
    wiki_path: Annotated[str | None, typer.Option("--wiki-path", help="Override the wiki directory.")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Auto-approve all HITL prompts.")] = False,
    eval_mode: Annotated[bool, typer.Option("--eval-mode", help="Skip the Daytona sandbox.")] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Show diagnostic output.")] = False,
) -> None:
    """Start an interactive chat session."""
    run_repl(
        thread_id=thread_id,
        ingest_mode=ingest_mode,
        wiki_path=wiki_path,
        yes=yes,
        eval_mode=eval_mode,
        debug=debug,
    )
