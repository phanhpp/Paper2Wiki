"""Browse and manage saved sessions (sessions.db).

These commands only touch the sessions DB (no agent/checkpointer), except ``resume`` which
hands off to the REPL, and ``prune`` which also evicts the matching checkpoints from
checkpoints.db (so it loads the async checkpointer — see its docstring).
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from src.cli._env import IngestMode

app = typer.Typer(help="Browse and manage saved sessions.")
console = Console()


def _fmt_ts(ts) -> str:
    if not ts:
        return "—"
    return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")


def _session_table(title: str) -> Table:
    table = Table(title=title, header_style="bold")
    table.add_column("Started", style="dim", no_wrap=True)
    table.add_column("Title")
    table.add_column("Source", no_wrap=True)
    table.add_column("Thread ID", style="cyan", no_wrap=True)
    return table


def _complete_session_ref(incomplete: str):
    """Tab-complete `resume`: suggest thread IDs (title as help) and titles (id as help).

    Runs in a per-TAB subprocess, so it imports lazily and never raises (a crash would break
    the shell). Newest sessions first; capped so completion stays snappy.
    """
    try:
        from src.sessions.sessions_db_setup import get_sessions_conn

        rows = get_sessions_conn().execute(
            "SELECT id, title FROM sessions ORDER BY started_at DESC LIMIT 50"
        ).fetchall()
    except Exception:
        return
    for sid, title in rows:
        if sid.startswith(incomplete):
            yield (sid, title or "untitled")
        if title and title.startswith(incomplete):
            yield (title, sid)


@app.command("ls")
def ls(
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max sessions to show.")] = 20,
    source: Annotated[str | None, typer.Option("--source", help="Filter by source (ingest/query/...).")] = None,
) -> None:
    """List recent sessions, newest first."""
    from src.sessions.sessions_db_setup import close_sessions_conn, get_sessions_conn

    conn = get_sessions_conn()
    try:
        if source:
            rows = conn.execute(
                "SELECT started_at, title, source, id FROM sessions "
                "WHERE source = ? ORDER BY started_at DESC LIMIT ?",
                (source, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT started_at, title, source, id FROM sessions "
                "ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    finally:
        close_sessions_conn()

    if not rows:
        console.print("[dim]No sessions found.[/]")
        return

    table = _session_table("Recent sessions")
    for started_at, title, src, sid in rows:
        table.add_row(_fmt_ts(started_at), title or "[dim]untitled[/]", src or "—", sid)
    console.print(table)


@app.command("search")
def search(
    query: Annotated[str, typer.Argument(help="FTS5 query over message content.")],
    limit: Annotated[int, typer.Option("--limit", "-n", help="Max sessions to show.")] = 20,
) -> None:
    """Full-text search message history; lists matching sessions."""
    from src.sessions.sessions_db_setup import close_sessions_conn, get_sessions_conn

    conn = get_sessions_conn()
    try:
        rows = conn.execute(
            "SELECT s.started_at, s.title, s.source, s.id "
            "FROM messages_fts f JOIN sessions s ON s.id = f.session_id "
            "WHERE messages_fts MATCH ? "
            "GROUP BY s.id ORDER BY s.started_at DESC LIMIT ?",
            (query, limit),
        ).fetchall()
    finally:
        close_sessions_conn()

    if not rows:
        console.print(f"[dim]No sessions matched[/] {query!r}.")
        return

    table = _session_table(f"Sessions matching {query!r}")
    for started_at, title, src, sid in rows:
        table.add_row(_fmt_ts(started_at), title or "[dim]untitled[/]", src or "—", sid)
    console.print(table)


@app.command("resume")
def resume(
    ref: Annotated[str, typer.Argument(
        help="Session to resume — a thread ID or a title (see `sessions ls`).",
        autocompletion=_complete_session_ref,
    )],
    ingest_mode: Annotated[IngestMode | None, typer.Option("--ingest-mode", help="Override ingest mode.")] = None,
    wiki_path: Annotated[str | None, typer.Option("--wiki-path", help="Override the wiki directory.")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Auto-approve all HITL prompts.")] = False,
    eval_mode: Annotated[bool, typer.Option("--eval-mode", help="Skip the Daytona sandbox.")] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Show diagnostic output.")] = False,
) -> None:
    """Resume a past session in the interactive REPL, by thread ID or title."""
    from src.sessions.session_manager import resolve_thread_id
    from src.sessions.sessions_db_setup import close_sessions_conn, get_sessions_conn

    conn = get_sessions_conn()
    try:
        thread_id = resolve_thread_id(conn, ref)
    finally:
        close_sessions_conn()

    if thread_id is None:
        typer.secho(
            f"No session matching {ref!r} (tried thread ID and title).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)

    from src.cli.commands.chat import run_repl

    run_repl(
        thread_id=thread_id,
        ingest_mode=ingest_mode,
        wiki_path=wiki_path,
        yes=yes,
        eval_mode=eval_mode,
        debug=debug,
    )


@app.command("rename")
def rename(
    ref: Annotated[str, typer.Argument(
        help="Session to rename — thread ID or current title.",
        autocompletion=_complete_session_ref,
    )],
    title: Annotated[str, typer.Argument(help="New title for the session.")],
) -> None:
    """Rename a session. Errors if the new title is already taken (no auto-numbering)."""
    from src.sessions.session_manager import resolve_thread_id
    from src.sessions.sessions_db_setup import close_sessions_conn, get_sessions_conn
    from src.sessions.title_manager import set_title_manual

    conn = get_sessions_conn()
    try:
        thread_id = resolve_thread_id(conn, ref)
        if thread_id is None:
            typer.secho(f"No session matching {ref!r}.", fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        try:
            applied = set_title_manual(conn, thread_id, title)
        except ValueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(1)
        console.print(f"Renamed [cyan]{thread_id}[/] → [bold]{applied}[/]")
    finally:
        close_sessions_conn()


@app.command("prune")
def prune(
    older_than_days: Annotated[int, typer.Option("--older-than-days", help="Age threshold in days.")] = 90,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Delete ended sessions older than the threshold (+ their checkpoints).

    Pruning is coupled across both SQLite DBs by ``thread_id``: every session
    deleted from sessions.db has its checkpoint state evicted from
    checkpoints.db in the same invocation. A pruned session is never resumed,
    so its checkpoint is pure garbage — keeping them in lockstep stops the two
    DBs from drifting into orphaned state.

    ``prune_sessions`` stays synchronous (blocking sqlite3); the checkpoint
    eviction (``prune_checkpoints``, async) runs here at the CLI boundary via
    ``asyncio.run`` — the one spot guaranteed to have no event loop running.
    This makes ``prune`` the only ``sessions`` subcommand that loads the async
    checkpointer; ``ls`` / ``search`` / ``resume`` stay agent-free and fast.
    """
    from src.sessions.session_manager import prune_sessions
    from src.sessions.sessions_db_setup import close_sessions_conn, get_sessions_conn

    conn = get_sessions_conn()
    try:
        deleted_ids = prune_sessions(conn, older_than_days=older_than_days, yes=yes)
    finally:
        close_sessions_conn()

    if not deleted_ids:
        return

    # Evict the matching checkpoints, driven by the exact deleted thread_ids.
    # sessions first, then checkpoints: if this leg fails midway a few
    # checkpoints linger (harmless — re-evicted next run), whereas the reverse
    # could orphan a still-resumable thread.
    from src.agents.agent import close_checkpointer, prune_checkpoints

    async def _evict() -> None:
        try:
            await prune_checkpoints(deleted_ids)
        finally:
            await close_checkpointer()  # flush WAL + release the handle in this loop

    asyncio.run(_evict())
    console.print(f"Evicted checkpoints for [cyan]{len(deleted_ids)}[/] threads.")
