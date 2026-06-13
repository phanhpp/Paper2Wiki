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
    return SimpleNamespace(type=role, content=content, tool_calls=None, name=None)


@pytest.fixture
def conn(tmp_path, monkeypatch):
    import src.sessions.sessions_db_setup as dbsetup

    monkeypatch.setattr(dbsetup, "SESSIONS_DIR", tmp_path)
    return dbsetup.setup_sessions_db()


@pytest.mark.unit
def test_save_session_is_idempotent_across_turns(conn):
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


@pytest.mark.unit
def test_save_session_distinct_content_kept(conn):
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
