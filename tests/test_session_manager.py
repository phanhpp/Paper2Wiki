"""Unit tests for session persistence — focus on idempotent re-saves.

`run_turn_stream_async` calls `save_session` at the end of every turn with the *full*
accumulated thread message list. These tests pin the behavior that re-saving a growing
thread does not duplicate message rows (or their FTS index entries).

Uses a real schema built in a temp dir (via the actual `setup_sessions_db` DDL, including
the `messages_fts` trigger) so the FTS de-dup is exercised, not mocked.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


def _msg(role: str, content: str):
    """Build a minimal message stand-in with the attributes save_session reads."""
    return SimpleNamespace(type=role, content=content, tool_calls=None, name=None)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """A fresh sessions.db (real schema + FTS trigger) in a temp dir, isolated per test."""
    import src.sessions.sessions_db_setup as dbsetup

    monkeypatch.setattr(dbsetup, "SESSIONS_DIR", tmp_path)
    return dbsetup.setup_sessions_db()


@pytest.mark.unit
def test_save_session_is_idempotent_across_turns(conn):
    """Re-saving a growing thread doesn't duplicate message or FTS rows (deterministic ids)."""
    from src.sessions.session_manager import save_session

    tid = "thread-x"
    turn1 = [_msg("human", "hello"), _msg("ai", "hi there")]
    # Next turn: earlier messages re-presented (LangGraph accumulates) plus two new ones.
    turn2 = turn1 + [_msg("human", "more"), _msg("ai", "reply")]

    save_session(conn, tid, turn1, started_at=1, flow_type="query")
    save_session(conn, tid, turn2, started_at=1, flow_type="query")

    n_msgs = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id=?", [tid]
    ).fetchone()[0]
    n_fts = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    n_sessions = conn.execute(
        "SELECT COUNT(*) FROM sessions WHERE id=?", [tid]
    ).fetchone()[0]

    assert n_msgs == 4, "duplicate message rows on re-save"
    assert n_fts == 4, "duplicate FTS rows on re-save"
    assert n_sessions == 1


def _add_session(conn, sid, title, started_at):
    """Insert a bare session row (id/title/started_at) for resolver/title tests."""
    conn.execute(
        "INSERT INTO sessions(id, title, started_at, status) VALUES (?, ?, ?, 'ended')",
        (sid, title, started_at),
    )
    conn.commit()


@pytest.mark.unit
def test_resolve_thread_id_by_id_title_and_missing(conn):
    """resolve_thread_id matches an exact thread_id, an exact title, and returns None otherwise."""
    from src.sessions.session_manager import resolve_thread_id

    _add_session(conn, "tid-1", "my project", 100)
    assert resolve_thread_id(conn, "tid-1") == "tid-1"        # exact id
    assert resolve_thread_id(conn, "my project") == "tid-1"   # exact title
    assert resolve_thread_id(conn, "nope") is None            # no match


@pytest.mark.unit
def test_resolve_thread_id_lineage(conn):
    """A base name resolves to the newest lineage member; a specific 'name #N' resolves to itself."""
    from src.sessions.session_manager import resolve_thread_id

    _add_session(conn, "a", "proj", 100)
    _add_session(conn, "b", "proj #2", 300)   # newest in lineage
    _add_session(conn, "c", "proj #3", 200)

    # Base name → most recent across the whole lineage (by started_at).
    assert resolve_thread_id(conn, "proj") == "b"
    # Specific member → that exact one, not the newest.
    assert resolve_thread_id(conn, "proj #3") == "c"
    assert resolve_thread_id(conn, "proj #9") is None


@pytest.mark.unit
def test_set_title_manual_success_collision_and_empty(conn):
    """set_title_manual sets a title, errors on collision (no auto-numbering), and rejects empty."""
    from src.sessions.title_manager import set_title_manual

    _add_session(conn, "s1", None, 100)
    _add_session(conn, "s2", "taken", 200)

    # success
    assert set_title_manual(conn, "s1", "My Project") == "My Project"
    assert conn.execute("SELECT title FROM sessions WHERE id='s1'").fetchone()[0] == "My Project"

    # collision with another session's title → error, no auto-numbering
    with pytest.raises(ValueError):
        set_title_manual(conn, "s1", "taken")

    # empty after sanitization → error
    with pytest.raises(ValueError):
        set_title_manual(conn, "s1", "   ")


@pytest.mark.unit
def test_save_session_distinct_content_kept(conn):
    """Distinct messages are stored separately (de-dup keys on content, not collapses it)."""
    from src.sessions.session_manager import save_session

    save_session(
        conn,
        "t1",
        [_msg("human", "alpha"), _msg("ai", "beta")],
        started_at=1,
    )
    rows = conn.execute(
        "SELECT content FROM messages WHERE session_id='t1' ORDER BY content"
    ).fetchall()
    assert [r[0] for r in rows] == ["alpha", "beta"]
