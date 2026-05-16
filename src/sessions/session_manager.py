# src/agents/session_manager.py
"""
Session lifecycle management for Paper2Wiki.

Handles saving sessions to sessions.db after each flow completes,
and manual pruning of old ended sessions.

Note: Auto-pruning is intentionally disabled (ships off by default,
same as Hermes). Session history is valuable for session_search recall.
Use prune_sessions() via CLI for one-off cleanup.

Typical usage after a flow completes:
    session_id = save_session(conn, thread_id, "ingest", messages, model, started_at)
    maybe_auto_title(conn, session_id, messages)
"""

import json
import time
import uuid
import logging
from datetime import datetime
from sqlite3 import Connection
from typing import Optional
from src.agents.llms import MODEL_CONFIG

logger = logging.getLogger(__name__)


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

    for msg in messages:
        raw_content = msg.content if hasattr(msg, 'content') else msg
        content = _serialize_message_content(raw_content)
        if not content:
            continue  # skip empty messages (e.g. tool call stubs with no text)

        tool_calls = None
        if hasattr(msg, 'tool_calls') and msg.tool_calls:
            tool_calls = json.dumps(msg.tool_calls)

        tool_name = getattr(msg, 'name', None)

        conn.execute("""
            INSERT INTO messages(id, session_id, role, content, tool_calls, tool_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [str(uuid.uuid4()), thread_id, msg.type, content, tool_calls, tool_name, now])

    conn.commit()
    logger.debug("Saved session %s (flow=%s, msgs=%d)", thread_id, flow_type, len(messages))
    return thread_id


def prune_sessions(conn: Connection, older_than_days: int = 90, yes: bool = False) -> None:
    """Delete ended sessions older than a threshold, with confirmation.

    Only deletes sessions with status='ended'. Active sessions are never pruned.
    Cascades to messages table via ON DELETE CASCADE foreign key constraint.
    Runs VACUUM after deletion to reclaim disk space (SQLite does not shrink
    the file on plain DELETE).

    Auto-pruning is intentionally NOT called at startup — session history is
    valuable for session_search recall. Call this explicitly via CLI.

    Args:
        conn:             sqlite3.Connection to sessions.db.
        older_than_days:  Delete sessions ended more than this many days ago.
        yes:              Skip confirmation prompt if True.
    """
    cutoff = int(time.time()) - (older_than_days * 86400)

    count = conn.execute("""
        SELECT COUNT(*) FROM sessions
        WHERE ended_at < ? AND status = 'ended'
    """, [cutoff]).fetchone()[0]

    if count == 0:
        print("No sessions to prune.")
        return

    if not yes:
        confirm = input(f"Delete {count} sessions older than {older_than_days} days? [y/N] ")
        if confirm.lower() != 'y':
            print("Cancelled.")
            return

    conn.execute("""
        DELETE FROM sessions
        WHERE ended_at < ? AND status = 'ended'
    """, [cutoff])
    # messages cascade-deleted via ON DELETE CASCADE
    conn.execute("VACUUM")
    conn.commit()
    print(f"Pruned {count} sessions.")