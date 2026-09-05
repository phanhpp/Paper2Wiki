# src/agents/sessions_db_setup.py
"""
SQLite setup for Any2Wiki session storage.

Separate from the LangGraph checkpointer (checkpoints.db).
This database stores clean session metadata + message history
for FTS5 session search, pruning, stats, and CLI management.

Tables:
    sessions      -- session metadata (id, title, source, model, timestamps)
    messages      -- full message history per session
    messages_fts  -- FTS5 virtual table for full-text search over message content
    meta          -- key-value store for internal state (e.g. last_prune timestamp)
"""

import sqlite3
from src.sessions.utils import REPO_ROOT

SESSIONS_DIR = REPO_ROOT / ".sessions"
SESSIONS_DIR.mkdir(exist_ok=True)  # ensure directory exists on import


def setup_sessions_db() -> sqlite3.Connection:
    """Create (or open) the sessions SQLite database and ensure schema exists.

    Safe to call multiple times — all CREATE statements use IF NOT EXISTS.
    Uses WAL journal mode for concurrent read access (needed for gateway phase).
    Enables foreign key enforcement so message rows cascade-delete with sessions.

    Returns:
        sqlite3.Connection with WAL mode and foreign keys enabled.
    """
    conn = sqlite3.connect(str(SESSIONS_DIR / "sessions.db"), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL") # WAL (Write-Ahead Log) — writes go to a separate log file first, readers can still read the old data simultaneously
    conn.execute("PRAGMA foreign_keys = ON")  # enable cascade deletes
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            title       TEXT UNIQUE,    -- NULL allowed, only non-NULL must be unique
            source      TEXT,           -- "ingest", "query", "code", "health"; "telegram" etc later
            model       TEXT,
            started_at  INTEGER,        -- unix timestamp
            ended_at    INTEGER,        -- unix timestamp
            status      TEXT            -- "active", "ended"
        );

        CREATE TABLE IF NOT EXISTS messages (
            id          TEXT PRIMARY KEY,
            session_id  TEXT REFERENCES sessions(id) ON DELETE CASCADE,
            role        TEXT,           -- "human", "ai", "tool"
            content     TEXT,
            tool_calls  TEXT,           -- JSON serialized list
            tool_name   TEXT,
            created_at  INTEGER         -- unix timestamp
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
            content,
            session_id  UNINDEXED,      -- stored but not indexed; used for joining back
            role        UNINDEXED
        );

        CREATE TABLE IF NOT EXISTS meta (
            key     TEXT PRIMARY KEY,
            value   TEXT
        );

        CREATE TRIGGER IF NOT EXISTS messages_fts_insert
        AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(content, session_id, role)
            VALUES (new.content, new.session_id, new.role);
        END;
    """)
    conn.commit()
    return conn


# Module-level singleton owned by sessions DB layer.
_sessions_conn = setup_sessions_db()


def get_sessions_conn() -> sqlite3.Connection:
    """Return the sessions DB connection, re-opening if it was closed."""
    global _sessions_conn
    if _sessions_conn is None:
        _sessions_conn = setup_sessions_db()
    return _sessions_conn


def close_sessions_conn() -> None:
    """Close the sessions DB connection cleanly."""
    global _sessions_conn
    if _sessions_conn is not None:
        _sessions_conn.close()
        _sessions_conn = None