# src/agents/session_manager.py
"""
Session lifecycle management for Any2Wiki.

Handles saving sessions to sessions.db after each flow completes,
and manual pruning of old ended sessions.

Note: Auto-pruning is intentionally disabled (ships off by default,
same as Hermes). Session history is valuable for session_search recall.
Use prune_sessions() via CLI for one-off cleanup.

Typical usage after a flow completes:
    session_id = save_session(conn, thread_id, "ingest", messages, model, started_at)
    maybe_auto_title(conn, session_id, messages)
"""

import re
import json
import time
import hashlib
import logging
from datetime import datetime
from sqlite3 import Connection
from typing import Optional
from src.agents.llms import MODEL_CONFIG

logger = logging.getLogger(__name__)


def resolve_thread_id(conn: Connection, ref: str) -> Optional[str]:
    """Resolve a session reference (a thread_id **or** a title) to a thread_id.

    Resolution order:
    1. Exact ``thread_id`` match — returned as-is.
    2. Title:
       - a **specific** lineage member (ref ends in ``" #N"``) → that exact title only.
       - a **base** name (no suffix) → the **most recent** session whose title is the base or
         ``"base #N"`` (mirrors "resume by name picks the latest in the lineage": resuming
         ``"my project"`` lands on the newest of ``"my project"`` / ``"my project #2"`` / …).

    Returns the resolved ``thread_id``, or ``None`` if nothing matches.
    """
    # 1. exact thread_id
    if conn.execute("SELECT 1 FROM sessions WHERE id = ?", (ref,)).fetchone():
        return ref

    # 2a. specific lineage member ("name #N") → exact title match only
    if re.match(r'^.* #\d+$', ref):
        row = conn.execute("SELECT id FROM sessions WHERE title = ?", (ref,)).fetchone()
        return row[0] if row else None

    # 2b. base name → most recent across the lineage (base or "base #N")
    escaped = ref.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    row = conn.execute(
        "SELECT id FROM sessions WHERE title = ? OR title LIKE ? ESCAPE '\\' "
        "ORDER BY started_at DESC LIMIT 1",
        (ref, f"{escaped} #%"),
    ).fetchone()
    return row[0] if row else None


def _stable_message_id(thread_id: str, index: int, role: str, content: str) -> str:
    """Deterministic message id so re-saving a thread is idempotent.

    LangGraph accumulates the full message list on a thread, and save_session() is called
    at the end of every turn — so a multi-turn session re-presents earlier messages each
    time. Keying each row on (thread_id, position, role, content) instead of a random UUID
    lets ``INSERT OR IGNORE`` skip rows already written (and, via the AFTER INSERT trigger,
    avoids duplicate FTS rows too). Messages are append-only and finalized by the time they
    are saved, so a given position maps to a stable row across turns.
    """
    digest = hashlib.sha256(
        f"{thread_id}\x00{index}\x00{role}\x00{content}".encode("utf-8")
    ).hexdigest()
    return digest[:32]


def _serialize_message_content(raw_content) -> str:
    """Convert message content into a SQLite-storable string."""
    if raw_content is None:
        return ""
    if isinstance(raw_content, str):
        return raw_content
    if isinstance(raw_content, (list, dict)):
        # Preserve structured blocks while keeping DB storage simple.
        return json.dumps(raw_content, ensure_ascii=False)
    return str(raw_content)


def save_session(
    conn: Connection,
    thread_id: str,
    messages: list,
    started_at: int,
    flow_type: Optional[str] = "query",
    model: Optional[str] = None,
) -> str:
    """Save a completed flow session to sessions.db.

    Writes session metadata and all messages. Messages are inserted into
    the messages table, which triggers automatic FTS5 indexing via the
    messages_fts_insert trigger.

    Does NOT generate a title — call maybe_auto_title() after this if desired.

    Args:
        conn:       sqlite3.Connection to sessions.db.
        thread_id:  LangGraph thread ID for this flow (stored for reference).
        flow_type:  Flow identifier. default is "query".
        messages:   Final message list from agent state (LangChain message objects).
        model:      Optional explicit model name; defaults to Haiku.
        started_at: Unix timestamp when the flow started.

    Returns:
        thread_id string for the newly created session.
    """

    now = int(time.time())
    resolved_model = model or MODEL_CONFIG["claude-haiku-4-5-20251001"]["model"]

    conn.execute("""
        INSERT OR IGNORE INTO sessions(id, source, model, started_at, ended_at, status)
        VALUES (?, ?, ?, ?, ?, 'ended')
    """, [thread_id, flow_type, resolved_model, started_at, now])

    for index, msg in enumerate(messages):
        raw_content = msg.content if hasattr(msg, 'content') else msg
        content = _serialize_message_content(raw_content)
        if not content:
            continue  # skip empty messages (e.g. tool call stubs with no text)

        tool_calls = None
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            tool_calls = json.dumps(msg.tool_calls)

        tool_name = getattr(msg, 'name', None)
        message_id = _stable_message_id(thread_id, index, msg.type, content)

        # INSERT OR IGNORE: re-saving the same thread (every turn) is a no-op for rows
        # already present, keeping the messages table and its FTS index duplicate-free.
        conn.execute("""
            INSERT OR IGNORE INTO messages(id, session_id, role, content, tool_calls, tool_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [message_id, thread_id, msg.type, content, tool_calls, tool_name, now])

    conn.commit()
    logger.debug("Saved session %s (flow=%s, msgs=%d)", thread_id, flow_type, len(messages))
    return thread_id


def prune_sessions(conn: Connection, older_than_days: int = 90, yes: bool = False) -> list[str]:
    """Delete ended sessions older than a threshold, with confirmation.

    Only deletes sessions with status='ended'. Active sessions are never pruned.
    Cascades to messages table via ON DELETE CASCADE foreign key constraint.
    Runs VACUUM after deletion to reclaim disk space (SQLite does not shrink
    the file on plain DELETE).

    Auto-pruning is intentionally NOT called at startup — session history is
    valuable for session_search recall. Call this explicitly via CLI.

    Stays fully synchronous on purpose: it does blocking ``sqlite3`` I/O, so
    ``async`` would buy nothing and would force every caller to ``await``. The
    matching checkpoint eviction (``prune_checkpoints``, which is async) is
    therefore *not* called from here — the CLI ``prune`` command runs it at the
    one place an event loop can be spun safely. See ``src/sessions/README.md``.

    Args:
        conn:             sqlite3.Connection to sessions.db.
        older_than_days:  Delete sessions ended more than this many days ago.
        yes:              Skip confirmation prompt if True.

    Returns:
        The thread_ids (sessions.id) that were deleted — feed these to
        ``src.agents.agent.prune_checkpoints`` so the matching checkpoint state
        is evicted by the same join key. Empty list if nothing was pruned (no
        matches, or the user cancelled).
    """
    cutoff = int(time.time()) - (older_than_days * 86400)

    # sessions.id IS the thread_id — capture the rows before DELETE so the caller
    # can evict exactly these threads from checkpoints.db (driven by the actual
    # deleted set, never by re-deriving "older than N days" a second time). Pull
    # title + started_at too so we can preview *which* sessions are at risk —
    # the title is what lets the user judge "keep or toss" before confirming.
    rows = conn.execute("""
        SELECT id, title, started_at FROM sessions
        WHERE ended_at < ? AND status = 'ended'
        ORDER BY started_at DESC
    """, [cutoff]).fetchall()
    deleted_ids = [row[0] for row in rows]

    if not deleted_ids:
        print("No sessions to prune.")
        return []

    # Preview the exact sessions about to be deleted (date — title), so the
    # decision is informed; answering "n" doubles as a no-op inspection.
    print(f"Sessions to prune ({len(rows)}):")
    PREVIEW_CAP = 50
    for _id, title, started_at in rows[:PREVIEW_CAP]:
        when = (
            datetime.fromtimestamp(int(started_at)).strftime("%Y-%m-%d %H:%M")
            if started_at else "—"
        )
        print(f"  {when}  {title or 'untitled'}")
    if len(rows) > PREVIEW_CAP:
        print(f"  … and {len(rows) - PREVIEW_CAP} more")

    if not yes:
        confirm = input(f"Delete these {len(deleted_ids)} sessions older than {older_than_days} days? [y/N] ")
        if confirm.lower() != 'y':
            print("Cancelled.")
            return []

    conn.execute("""
        DELETE FROM sessions
        WHERE ended_at < ? AND status = 'ended'
    """, [cutoff])
    # messages cascade-deleted via ON DELETE CASCADE
    conn.commit()  # close the write transaction — VACUUM cannot run inside one
    conn.execute("VACUUM")
    print(f"Pruned {len(deleted_ids)} sessions.")
    return deleted_ids