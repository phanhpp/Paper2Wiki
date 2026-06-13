"""Browse and manage saved sessions (sessions.db).

These commands only touch the sessions DB (no agent/checkpointer), except ``resume`` which
hands off to the REPL.
"""

from __future__ import annotations

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
    thread_id: Annotated[str, typer.Argument(help="Thread ID to resume (see `sessions ls`).")],
    ingest_mode: Annotated[IngestMode | None, typer.Option("--ingest-mode", help="Override ingest mode.")] = None,
    wiki_path: Annotated[str | None, typer.Option("--wiki-path", help="Override the wiki directory.")] = None,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Auto-approve all HITL prompts.")] = False,
    eval_mode: Annotated[bool, typer.Option("--eval-mode", help="Skip the Daytona sandbox.")] = False,
    debug: Annotated[bool, typer.Option("--debug", help="Show diagnostic output.")] = False,
) -> None:
    """Resume a past session in the interactive REPL."""
    from src.cli.commands.chat import run_repl

    run_repl(
        thread_id=thread_id,
        ingest_mode=ingest_mode,
        wiki_path=wiki_path,
        yes=yes,
        eval_mode=eval_mode,
        debug=debug,
    )


@app.command("prune")
def prune(
    older_than_days: Annotated[int, typer.Option("--older-than-days", help="Age threshold in days.")] = 90,
    yes: Annotated[bool, typer.Option("--yes", "-y", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Delete ended sessions older than the threshold."""
    from src.sessions.session_manager import prune_sessions
    from src.sessions.sessions_db_setup import close_sessions_conn, get_sessions_conn

    conn = get_sessions_conn()
    try:
        prune_sessions(conn, older_than_days=older_than_days, yes=yes)
    finally:
        close_sessions_conn()
