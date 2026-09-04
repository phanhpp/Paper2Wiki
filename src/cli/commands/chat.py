"""``paper2wiki repl`` and ``paper2wiki chat`` — the two ways to talk to the agent.

``chat`` runs one message and exits. ``repl`` keeps a prompt open and streams turn after
turn.

The difference that matters: ``repl`` builds the agent **once** and reuses it for every
turn, because building it provisions a Daytona sandbox over the network. ``chat`` builds
one per invocation, which is why a series of one-shot calls is slower than a REPL.

Inside the REPL you can also type meta-commands (``/title``, ``/new``, ``/open``,
``/help``, ``/exit``); anything else is sent to the agent.

Functions:
    chat(...) / repl(...)     — the Typer commands: declare the flags, forward them on.
    run_chat(...)             — build an agent, stream one message, exit.
    run_repl(...)             — build an agent once, then loop: read input, stream a turn.
    _new_thread_id()          — create an id for a new session.
    _apply_title(...)         — apply a /title, reporting errors instead of raising.
"""

from __future__ import annotations

import time
import typer
from langchain_core.utils.uuid import uuid7
from typing import Annotated
from src.cli._env import IngestMode, apply_env, require_keys, setup_logging
from src.cli._runtime import run_async

_REPL_HELP = """\
Commands:
  /title <name>   set a memorable title for this session
  /title          show the current title
  /new            start a fresh session (new thread + sandbox)
  /help           show this help
  /exit           quit (also: quit, exit, bye, :q, or Ctrl-D)
  /open           open the last turn's full tool output in a pager (also: /last, Ctrl-O)
"""


def _new_thread_id() -> str:
    """Create an id for a new session.

    uuid7 has the creation time built into it, so sorting the ids sorts the sessions
    oldest-first — handy when listing them.
    """
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
    model: str | None = None,
    yes: bool,
    eval_mode: bool,
    debug: bool,
    no_save: bool = False,
) -> None:
    """One-shot: run a single message through a fresh supervisor and exit."""
    setup_logging(debug)
    apply_env(ingest_mode, wiki_path, model)
    require_keys(eval_mode)

    # Lazy imports: must follow apply_env() so tool registration sees the ingest mode.
    from src.agents.agent import create_supervisor
    from src.agents.stream import run_turn_stream_async
    from src.cli.renderer import RichRenderer

    tid = thread_id or _new_thread_id()
    renderer = RichRenderer(auto_approve=yes, debug=debug)

    async def _run() -> None:
        agent = await create_supervisor(tid, eval_mode=eval_mode)
        await run_turn_stream_async(
            message, agent=agent, thread_id=tid, renderer=renderer, persist=not no_save,
        )

    run_async(_run())


def run_repl(
    *,
    thread_id: str | None,
    ingest_mode: IngestMode | None,
    wiki_path: str | None,
    model: str | None = None,
    yes: bool,
    eval_mode: bool,
    debug: bool,
    no_save: bool = False,
) -> None:
    """Interactive loop: build the supervisor once, stream each typed turn."""
    t_start = time.perf_counter()
    setup_logging(debug)
    apply_env(ingest_mode, wiki_path, model)
    require_keys(eval_mode)

    # Light imports only — keep these cheap so the banner can paint before the slow
    # agent build (the heavy LangGraph/Daytona imports are deferred into _loop below).
    from prompt_toolkit import PromptSession
    from prompt_toolkit.application import run_in_terminal
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.key_binding import KeyBindings

    from src.cli.renderer import RichRenderer
    from src.cli.welcome import print_welcome
    from src.ingest_mode import get_ingest_mode

    renderer = RichRenderer(auto_approve=yes, debug=debug)
    console = renderer.console
    current = {"tid": thread_id or _new_thread_id()}
    # A /title issued before the first turn is queued: the session row doesn't exist until
    # save_session runs, so we apply it right after the next turn completes.
    pending_title: str | None = None

    # Banner first: the agent build (importing LangGraph and, unless --eval-mode,
    # provisioning a Daytona sandbox over the network) is the slow part — show the
    # header immediately so startup doesn't look frozen.
    print_welcome(
        console,
        thread_id=current["tid"],
        ingest_mode=get_ingest_mode(),
        no_save=no_save,
        eval_mode=eval_mode,
    )
    if debug:
        console.print(f"[dim][timing] env + light imports + banner: {time.perf_counter() - t_start:.1f}s[/]")

    async def _loop() -> None:
        nonlocal pending_title
        # Deferred heavy imports + agent construction, under a spinner whose label tracks
        # the current phase so the (long) wait shows what it's actually doing.
        with console.status("[green]Loading agent framework…[/]", spinner="dots") as status:
            t_imp0 = time.perf_counter()
            from src.agents.agent import create_supervisor
            from src.agents.stream import run_turn_stream_async
            from src.sessions.sessions_db_setup import get_sessions_conn
            t_imp1 = time.perf_counter()
            if eval_mode:
                status.update("[green]Building agent…[/]", spinner="dots")
            else:
                status.update(
                    "[green]Provisioning Daytona sandbox…[/] [dim][/]",
                    spinner="earth",
                )
            agent = await create_supervisor(current["tid"], eval_mode=eval_mode)
            t_build = time.perf_counter()
        if debug:
            console.print(
                f"[dim][timing] heavy imports: {t_imp1 - t_imp0:.1f}s · "
                f"agent build{'' if eval_mode else ' (+Daytona)'}: {t_build - t_imp1:.1f}s · "
                f"total to ready: {t_build - t_start:.1f}s[/]"
            )

        # Ctrl-O pages the full output of the last turn's tools (the inline view
        # only shows a truncated preview). run_in_terminal suspends the prompt,
        # runs the pager, then restores it.
        kb = KeyBindings()

        @kb.add("c-o")
        def _open_full(event) -> None:
            run_in_terminal(renderer.open_last_tool_output)

        session: PromptSession = PromptSession(
            history=InMemoryHistory(), key_bindings=kb,
        )
        console.print("[dim]Ready — type a message below.[/]\n")

        while True:
            try:
                user_in = (await session.prompt_async("you ❯ ")).strip()
            except (EOFError, KeyboardInterrupt):
                break

            if not user_in:
                continue
            # Accept bare quit/exit/bye too — typing them almost always means "leave",
            # not "send this word to the model". Slash forms remain the documented way.
            if user_in in ("/exit", "/quit", "exit", "quit", "bye", ":q"):
                break
            if user_in == "/help":
                console.print(_REPL_HELP)
                continue
            if user_in in ("/open", "/last"):
                # Same as Ctrl-O, but always works — no terminal keybinding needed.
                renderer.open_last_tool_output()
                continue
            if user_in == "/new":
                current["tid"] = _new_thread_id()
                pending_title = None
                with console.status("[green]Starting new session…[/]", spinner="dots"):
                    agent = await create_supervisor(current["tid"], eval_mode=eval_mode)
                console.print(f"[dim]New session {current['tid']}[/]\n")
                continue
            if user_in.split(maxsplit=1)[0] == "/title":
                if no_save:
                    console.print("[dim](--no-save is on — titles aren't persisted)[/]")
                    continue
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

            # Dispatch one turn. The renderer drives the per-turn UI lifecycle:
            #   user hits enter
            #     → on_turn_start()  spinner Live starts ("Thinking…", transient)
            #     → on_token() [1st] spinner stops, Markdown Live starts
            #     → on_token() × N   Markdown buffer grows in place
            #     → on_turn_end()    Markdown Live stops; text stays on screen
            #   → loop back to the prompt
            # Skip the LLM auto-titler when a manual title is queued — it would just be
            # overwritten below, wasting a model call.
            try:
                await run_turn_stream_async(
                    user_in, agent=agent, thread_id=current["tid"], renderer=renderer,
                    auto_title=pending_title is None, persist=not no_save,
                )
            finally:
                # If the turn raised mid-stream, _end_live never ran — make sure the
                # terminal's echo is back on so the next prompt isn't silent.
                renderer.restore_echo()

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
    model: Annotated[str | None, typer.Option("--model", "-m", help="Override the base model, e.g. openai:gpt-4o.")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Auto-approve all HITL prompts.")] = False,
    eval_mode: Annotated[bool, typer.Option("--eval-mode", help="Skip the Daytona sandbox.")] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Show diagnostic output.")] = False,
    no_save: Annotated[bool, typer.Option("--no-save", help="Don't persist to sessions.db (no title, no history).")] = False,
) -> None:
    """Typer entry for ``paper2wiki chat MSG``: run one message and exit.

    Only declares the flags and passes them to ``run_chat``. Kept separate so the
    flag definitions sit next to Typer and the logic stays easy to test on its own.
    """
    run_chat(
        message,
        thread_id=thread_id,
        ingest_mode=ingest_mode,
        wiki_path=wiki_path,
        model=model,
        yes=yes,
        eval_mode=eval_mode,
        debug=debug,
        no_save=no_save,
    )


def repl(
    thread_id: Annotated[str | None, typer.Option("--thread-id", "-t", help="Resume an existing thread.")] = None,
    ingest_mode: Annotated[IngestMode | None, typer.Option("--ingest-mode", help="Override ingest mode.")] = None,
    wiki_path: Annotated[str | None, typer.Option("--wiki-path", help="Override the wiki directory.")] = None,
    model: Annotated[str | None, typer.Option("--model", "-m", help="Override the base model, e.g. openai:gpt-4o.")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Auto-approve all HITL prompts.")] = False,
    eval_mode: Annotated[bool, typer.Option("--eval-mode", help="Skip the Daytona sandbox.")] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Show diagnostic output.")] = False,
    no_save: Annotated[bool, typer.Option("--no-save", help="Don't persist to sessions.db (no title, no history).")] = False,
) -> None:
    """Typer entry for ``paper2wiki repl``: start an interactive chat session.

    Only declares the flags and passes them to ``run_repl`` — see that function for
    how each turn is streamed.
    """
    run_repl(
        thread_id=thread_id,
        ingest_mode=ingest_mode,
        wiki_path=wiki_path,
        model=model,
        yes=yes,
        eval_mode=eval_mode,
        debug=debug,
        no_save=no_save,
    )
