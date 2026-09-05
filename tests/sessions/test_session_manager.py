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


def _add_ended_session(conn, sid, ended_at):
    """Insert an ended session row with an explicit ended_at (for prune tests)."""
    conn.execute(
        "INSERT INTO sessions(id, started_at, ended_at, status) VALUES (?, ?, ?, 'ended')",
        (sid, ended_at - 10, ended_at),
    )
    conn.commit()


@pytest.mark.unit
def test_prune_sessions_returns_deleted_ids(conn):
    """prune deletes matching ended sessions and returns exactly their thread_ids."""
    from src.sessions.session_manager import prune_sessions

    _add_ended_session(conn, "old-1", ended_at=100)
    _add_ended_session(conn, "old-2", ended_at=200)

    deleted = prune_sessions(conn, older_than_days=0, yes=True)

    assert sorted(deleted) == ["old-1", "old-2"]
    remaining = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert remaining == 0


@pytest.mark.unit
def test_prune_sessions_previews_titles_before_confirm(conn, monkeypatch, capsys):
    """The confirm prompt is preceded by a preview of each session's date + title."""
    from src.sessions import session_manager

    conn.execute(
        "INSERT INTO sessions(id, title, started_at, ended_at, status) "
        "VALUES ('old-1', 'important paper notes', 90, 100, 'ended')"
    )
    conn.commit()
    monkeypatch.setattr("builtins.input", lambda _: "n")  # inspect, don't delete

    deleted = session_manager.prune_sessions(conn, older_than_days=0, yes=False)
    out = capsys.readouterr().out

    assert deleted == []  # cancelled
    assert "Sessions to prune (1)" in out
    assert "important paper notes" in out  # title shown so the user can judge
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


@pytest.mark.unit
def test_prune_sessions_no_match_returns_empty(conn):
    """No eligible sessions → empty list, nothing deleted."""
    from src.sessions.session_manager import prune_sessions

    # Active sessions are never eligible regardless of age.
    conn.execute(
        "INSERT INTO sessions(id, started_at, ended_at, status) VALUES ('live', 1, 2, 'active')"
    )
    conn.commit()

    assert prune_sessions(conn, older_than_days=0, yes=True) == []
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


@pytest.mark.unit
def test_prune_sessions_cancelled_returns_empty(conn, monkeypatch):
    """Declining the confirmation returns [] and deletes nothing."""
    from src.sessions import session_manager

    _add_ended_session(conn, "old-1", ended_at=100)
    monkeypatch.setattr("builtins.input", lambda _: "n")

    assert session_manager.prune_sessions(conn, older_than_days=0, yes=False) == []
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


@pytest.mark.unit
async def test_prune_checkpoints_empty_is_noop():
    """prune_checkpoints([]) returns without ever touching the checkpointer singleton."""
    import src.agents.agent as agent

    async def _boom():
        raise AssertionError("checkpointer must not be initialized for an empty prune")

    # If the guard is wrong this raises; a clean return proves the no-op path.
    import unittest.mock as mock

    with mock.patch.object(agent, "_get_async_checkpointer", _boom):
        await agent.prune_checkpoints([])


@pytest.mark.unit
async def test_prune_checkpoints_evicts_each_thread(monkeypatch):
    """Each thread_id is evicted via the checkpointer's adelete_thread (full deletion)."""
    import src.agents.agent as agent

    deleted: list[str] = []

    class _FakeCheckpointer:
        async def adelete_thread(self, tid):
            deleted.append(tid)

    async def _fake_get():
        return _FakeCheckpointer()

    monkeypatch.setattr(agent, "_get_async_checkpointer", _fake_get)

    await agent.prune_checkpoints(["t1", "t2", "t3"])
    assert deleted == ["t1", "t2", "t3"]


@pytest.mark.unit
def test_prune_command_couples_checkpoint_eviction(tmp_path, monkeypatch):
    """The CLI `prune` command feeds the deleted thread_ids straight to prune_checkpoints."""
    import src.agents.agent as agent
    import src.sessions.sessions_db_setup as dbsetup
    from src.cli.commands import sessions as cmd

    monkeypatch.setattr(dbsetup, "SESSIONS_DIR", tmp_path)
    seed = dbsetup.setup_sessions_db()
    _add_ended_session(seed, "old-1", ended_at=100)
    _add_ended_session(seed, "old-2", ended_at=200)
    dbsetup.close_sessions_conn()  # let the command open its own connection

    evicted: list[str] = []

    async def _fake_prune_checkpoints(ids):
        evicted.extend(ids)

    async def _fake_close():
        pass

    monkeypatch.setattr(agent, "prune_checkpoints", _fake_prune_checkpoints)
    monkeypatch.setattr(agent, "close_checkpointer", _fake_close)

    cmd.prune(older_than_days=0, yes=True)

    assert sorted(evicted) == ["old-1", "old-2"]


class _FakeExec:
    """Stands in for aiosqlite's execute() result: both awaitable and async-CM."""

    def __init__(self, rows):
        self._rows = rows

    async def fetchall(self):
        return self._rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __await__(self):
        async def _coro():
            return self

        return _coro().__await__()


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows
        self.vacuumed = False

    def execute(self, sql, *a):
        if "VACUUM" in sql.upper():
            self.vacuumed = True
        return _FakeExec(self._rows)

    async def commit(self):
        pass


class _FakeCheckpointer:
    def __init__(self, rows=()):
        self.conn = _FakeConn(list(rows))
        self.deleted: list[str] = []

    async def adelete_thread(self, tid):
        self.deleted.append(tid)


def _v6_for(unix_ts: float) -> str:
    """Build a UUID-v6-shaped checkpoint_id encoding the given unix timestamp."""
    ticks = int(unix_ts * 1e7) + 0x01b21dd213814000
    time_high = (ticks >> 28) & 0xFFFFFFFF
    time_mid = (ticks >> 12) & 0xFFFF
    time_low = ticks & 0x0FFF
    return f"{time_high:08x}-{time_mid:04x}-{0x6000 | time_low:04x}-8000-000000000000"


@pytest.mark.unit
async def test_find_orphan_checkpoint_threads_filters_known(monkeypatch):
    """Returns (thread_id, last_activity) for threads absent from the known ids."""
    import src.agents.agent as agent

    ck = _v6_for(1_000_000)
    fake = _FakeCheckpointer(rows=[("t1", ck), ("t2", ck), ("t3", ck)])

    async def _get():
        return fake

    monkeypatch.setattr(agent, "_get_async_checkpointer", _get)

    records = await agent.find_orphan_checkpoint_threads({"t2"})
    assert [tid for tid, _ in records] == ["t1", "t3"]
    assert all(abs(ts - 1_000_000) < 1 for _, ts in records)


@pytest.mark.unit
async def test_find_orphan_checkpoint_threads_decodes_last_activity(monkeypatch):
    """Each orphan carries a decoded last-activity timestamp (None if undecodable)."""
    import time

    import src.agents.agent as agent

    now = time.time()
    fake = _FakeCheckpointer(rows=[
        ("old", _v6_for(now - 10 * 86400)),
        ("garbled", "not-a-uuid"),
    ])

    async def _get():
        return fake

    monkeypatch.setattr(agent, "_get_async_checkpointer", _get)

    by_id = dict(await agent.find_orphan_checkpoint_threads(set()))
    assert abs(by_id["old"] - (now - 10 * 86400)) < 2
    assert by_id["garbled"] is None


@pytest.mark.unit
def test_checkpoint_unix_roundtrip():
    """_checkpoint_unix decodes a v6 id back to its timestamp; bad input → None."""
    import src.agents.agent as agent

    ts = 1_700_000_123.0
    decoded = agent._checkpoint_unix(_v6_for(ts))
    assert decoded is not None and abs(decoded - ts) < 1.0
    assert agent._checkpoint_unix("not-a-uuid") is None


@pytest.mark.unit
async def test_prune_checkpoints_vacuum_flag(monkeypatch):
    """vacuum=True deletes each thread then runs a single VACUUM; default does not."""
    import src.agents.agent as agent

    fake = _FakeCheckpointer()

    async def _get():
        return fake

    monkeypatch.setattr(agent, "_get_async_checkpointer", _get)

    await agent.prune_checkpoints(["a", "b"], vacuum=True)
    assert fake.deleted == ["a", "b"]
    assert fake.conn.vacuumed is True


@pytest.mark.unit
def test_prune_orphans_refuses_on_empty_sessions_db(tmp_path, monkeypatch):
    """Empty sessions.db would make everything look orphaned → refuse, never delete."""
    import typer

    import src.sessions.sessions_db_setup as dbsetup
    from src.cli.commands import sessions as cmd

    monkeypatch.setattr(dbsetup, "SESSIONS_DIR", tmp_path)
    dbsetup.setup_sessions_db()  # empty
    dbsetup.close_sessions_conn()

    with pytest.raises(typer.Exit):
        cmd.prune_orphans(apply=True, vacuum=True, yes=True)


@pytest.mark.unit
def test_prune_orphans_dry_run_does_not_delete(tmp_path, monkeypatch):
    """Default (no --apply) lists orphans but never calls prune_checkpoints."""
    import src.agents.agent as agent
    import src.sessions.sessions_db_setup as dbsetup
    from src.cli.commands import sessions as cmd

    monkeypatch.setattr(dbsetup, "SESSIONS_DIR", tmp_path)
    seed = dbsetup.setup_sessions_db()
    _add_ended_session(seed, "s1", ended_at=100)  # non-empty → passes the guard
    dbsetup.close_sessions_conn()

    async def _fake_find(known):
        return [("orphan-1", 1_000_000.0), ("orphan-2", 1_000_000.0)]

    def _must_not_delete(*_a, **_k):
        raise AssertionError("dry run must not delete")

    async def _noop():
        pass

    monkeypatch.setattr(agent, "find_orphan_checkpoint_threads", _fake_find)
    monkeypatch.setattr(agent, "prune_checkpoints", _must_not_delete)
    monkeypatch.setattr(agent, "close_checkpointer", _noop)

    cmd.prune_orphans(apply=False, vacuum=False, older_than=0.0, full=False, yes=False)  # no raise = pass


@pytest.mark.unit
def test_prune_orphans_apply_deletes(tmp_path, monkeypatch):
    """--apply -y evicts exactly the orphan set, forwarding the vacuum flag."""
    import src.agents.agent as agent
    import src.sessions.sessions_db_setup as dbsetup
    from src.cli.commands import sessions as cmd

    monkeypatch.setattr(dbsetup, "SESSIONS_DIR", tmp_path)
    seed = dbsetup.setup_sessions_db()
    _add_ended_session(seed, "s1", ended_at=100)
    dbsetup.close_sessions_conn()

    async def _fake_find(known):
        return [("orphan-1", 1_000_000.0), ("orphan-2", 1_000_000.0)]

    calls = {}

    async def _fake_prune(ids, *, vacuum=False):
        calls["ids"] = ids
        calls["vacuum"] = vacuum

    async def _noop():
        pass

    monkeypatch.setattr(agent, "find_orphan_checkpoint_threads", _fake_find)
    monkeypatch.setattr(agent, "prune_checkpoints", _fake_prune)
    monkeypatch.setattr(agent, "close_checkpointer", _noop)

    cmd.prune_orphans(apply=True, vacuum=True, older_than=0.0, full=False, yes=True)
    assert calls == {"ids": ["orphan-1", "orphan-2"], "vacuum": True}


@pytest.mark.unit
def test_prune_orphans_recency_filter_excludes_and_explains(tmp_path, monkeypatch, capsys):
    """--older-than that matches nothing still reports the orphans exist, and deletes nothing."""
    import time

    import src.agents.agent as agent
    import src.sessions.sessions_db_setup as dbsetup
    from src.cli.commands import sessions as cmd

    monkeypatch.setattr(dbsetup, "SESSIONS_DIR", tmp_path)
    seed = dbsetup.setup_sessions_db()
    _add_ended_session(seed, "s1", ended_at=100)
    dbsetup.close_sessions_conn()

    now = time.time()

    async def _fake_find(known):
        return [("fresh", now - 60)]  # active a minute ago

    def _must_not_delete(*_a, **_k):
        raise AssertionError("recency filter excluded all → must not delete")

    async def _noop():
        pass

    monkeypatch.setattr(agent, "find_orphan_checkpoint_threads", _fake_find)
    monkeypatch.setattr(agent, "prune_checkpoints", _must_not_delete)
    monkeypatch.setattr(agent, "close_checkpointer", _noop)

    # older_than=1 day → the 1-minute-old thread is excluded; nothing eligible.
    cmd.prune_orphans(apply=True, vacuum=False, older_than=1.0, full=False, yes=True)
    out = capsys.readouterr().out
    assert "No orphans inactive" in out and "1 orphans exist" in out


@pytest.mark.unit
def test_prune_command_skips_eviction_when_nothing_pruned(tmp_path, monkeypatch):
    """No deleted sessions → prune_checkpoints is never called (no checkpointer load)."""
    import src.agents.agent as agent
    import src.sessions.sessions_db_setup as dbsetup
    from src.cli.commands import sessions as cmd

    monkeypatch.setattr(dbsetup, "SESSIONS_DIR", tmp_path)
    dbsetup.setup_sessions_db()  # empty db
    dbsetup.close_sessions_conn()

    def _must_not_call(*_a, **_k):
        raise AssertionError("prune_checkpoints called despite empty prune set")

    monkeypatch.setattr(agent, "prune_checkpoints", _must_not_call)
    cmd.prune(older_than_days=0, yes=True)


@pytest.mark.unit
def test_stats_counts_and_prune_buckets(tmp_path, monkeypatch, capsys):
    """`sessions stats` reports totals and per-age prunable counts matching prune's predicate."""
    import time

    import src.sessions.sessions_db_setup as dbsetup
    from src.cli.commands import sessions as cmd

    monkeypatch.setattr(dbsetup, "SESSIONS_DIR", tmp_path)
    seed = dbsetup.setup_sessions_db()

    now = int(time.time())
    day = 86400
    # ended sessions at varying ages, plus one active (never prunable).
    _add_ended_session(seed, "e-100d", ended_at=now - 100 * day)  # > 7, 30, 90
    _add_ended_session(seed, "e-45d", ended_at=now - 45 * day)    # > 7, 30
    _add_ended_session(seed, "e-3d", ended_at=now - 3 * day)      # none
    seed.execute(
        "INSERT INTO sessions(id, started_at, ended_at, status) VALUES ('live', ?, NULL, 'active')",
        (now,),
    )
    seed.commit()
    dbsetup.close_sessions_conn()

    cmd.stats()
    out = capsys.readouterr().out

    assert "4 sessions" in out
    assert "1 active" in out and "3 ended" in out
    # Buckets: >7d → 2 (100d, 45d), >30d → 2, >90d → 1.
    assert "sessions prune --older-than-days 90" in out


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
