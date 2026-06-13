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
  /title <name>   set a memorable title for this session
  /title          show the current title
  /new            start a fresh session (new thread + sandbox)
  /help           show this help
  /exit           quit (also Ctrl-D)
"""


def _new_thread_id() -> str:
    return str(uuid7())


def _apply_title(conn, thread_id: str, title: str, console) -> bool:
    """Apply a manual title to an existing session row; print the outcome.

    Returns True on success. Errors (collision / invalid) are reported, not raised, so a bad
    /title never drops the user out of the REPL.
    """
    from src.sessions.title_manager import set_title_manual

    try:
        applied = set_title_manual(conn, thread_id, title)
    except ValueError as exc:
        console.print(f"[red]Title not set:[/] {exc}")
        return False
    console.print(f"[dim]Title set: {applied}[/]")
    return True


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
    from src.sessions.sessions_db_setup import get_sessions_conn

    renderer = RichRenderer(auto_approve=yes, debug=debug)
    console = renderer.console
    current = {"tid": thread_id or _new_thread_id()}
    # A /title issued before the first turn is queued: the session row doesn't exist until
    # save_session runs, so we apply it right after the next turn completes.
    pending_title: str | None = None

    async def _loop() -> None:
        nonlocal pending_title
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
                pending_title = None
                agent = await create_supervisor(current["tid"], eval_mode=eval_mode)
                console.print(f"[dim]New session {current['tid']}[/]\n")
                continue
            if user_in.split(maxsplit=1)[0] == "/title":
                arg = user_in[len("/title"):].strip()
                conn = get_sessions_conn()
                row = conn.execute(
                    "SELECT title FROM sessions WHERE id = ?", (current["tid"],)
                ).fetchone()
                if not arg:  # show current title
                    if row is None:
                        console.print("[dim](no session yet — send a message first)[/]")
                    else:
                        console.print(f"[dim]Current title: {row[0] or 'untitled'}[/]")
                elif row is None:  # session not saved yet → queue it
                    pending_title = arg
                    console.print(f"[dim]Title queued: {arg} (applies after your first message)[/]")
                else:
                    _apply_title(conn, current["tid"], arg, console)
                continue

            # Skip the LLM auto-titler when a manual title is queued — it would just be
            # overwritten below, wasting a model call.
            await run_turn_stream_async(
                user_in, agent=agent, thread_id=current["tid"], renderer=renderer,
                auto_title=pending_title is None,
            )

            if pending_title is not None:  # apply a title queued before the session existed
                _apply_title(get_sessions_conn(), current["tid"], pending_title, console)
                pending_title = None

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
