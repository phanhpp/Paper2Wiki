# `src/sessions/` — session catalog & history

This package is the **human-facing catalog** of agent conversations: a clean record of every
session's messages, plus full-text search, titling, and pruning. It powers the
`paper2wiki sessions ...` CLI commands.

## Notes

1. **Thread**: A thread is one conversation session. It has a `thread_id` and belongs to a `user_id` via metadata. All checkpoints for a session live under one thread.

2. **FTS5** builds a lookup table of every word → which rows contain it = the inverted index

word         rows that contain it
─────────────────────────────────
"docker"   → [1, 3, 847, 2341]
"compose"  → [3, 102, 847]
"deploy"   → [1, 55, 203]
"model"    → [2, 19, 88]

3. **Why virtual table:**

This inverted index is a completely different data structure from a normal SQL table. It can't just live in regular rows and columns. FTS5 needs to manage its own internal storage format.

A **virtual table** is SQLite's way of saying: "this looks like a table to you, but underneath it's running completely custom code."

```sql
-- looks like a table
SELECT * FROM messages_fts WHERE messages_fts MATCH 'docker'

-- but underneath, FTS5 module is:
-- 1. looking up "docker" in its inverted index
-- 2. returning matching rows with relevance scores
-- not scanning anything
```

4.**WAL mode:**

Normal SQLite — when you write, the file is locked. Nobody can read while writing:

```text
Writer writing → all readers blocked → wait
```

WAL (Write-Ahead Log) — writes go to a separate log file first, readers can still read the old data simultaneously:

```text
Writer → writes to WAL log file
Readers → read from main db file simultaneously
No blocking
```

## Two databases, one join key

There are **two** SQLite files under `.sessions/`, and they do different jobs:

| DB | Owned by | Purpose |
|---|---|---|
| `checkpoints.db` | `src/agents/agent.py` (LangGraph `AsyncSqliteSaver`) | the **machine state** needed to *resume* a conversation — graph nodes, pending interrupts, full LangGraph state |
| `sessions.db` | **this package** | the **human view** — clean message history, titles, FTS search, metadata for browsing/pruning |

They are linked by one value: **`thread_id`**. The same `thread_id` is the primary key of the
`sessions` table here *and* the key LangGraph uses in `checkpoints.db` *and* the key the Daytona
sandbox is registered under. `sessions.db` never drives execution — it's a catalog you read; the
checkpointer is what actually restores a run.

```
            thread_id  (the join key)
        ┌───────────────┴────────────────┐
        ▼                                 ▼
 checkpoints.db                       sessions.db
 (resume the agent:                   (browse / search / title:
  create_supervisor(thread_id))        this package)
```

## Files

| File | Responsibility |
|---|---|
| `sessions_db_setup.py` | Schema + connection singleton. `setup_sessions_db()` creates tables (idempotent, `IF NOT EXISTS`); `get_sessions_conn()` / `close_sessions_conn()` manage the module-level connection. `SESSIONS_DIR` is `<repo>/.sessions`. |
| `session_manager.py` | `save_session()` writes session metadata + messages (idempotently — see below); `prune_sessions()` deletes old ended sessions (manual, never automatic). |
| `title_manager.py` | Human-friendly titles: `maybe_auto_title()` (fire-and-forget LLM titling in a background thread), `set_session_title()` (auto-titling — collisions auto-numbered), `set_title_manual()` (user-chosen — collisions **error**, used by `/title` and `sessions rename`), `sanitize_title()`, `get_next_title_in_lineage()`. |
| `utils.py` | `REPO_ROOT` path constant. |

## Schema (`sessions.db`)

```
sessions(id PK, title UNIQUE, source, model, started_at, ended_at, status)
messages(id PK, session_id → sessions.id ON DELETE CASCADE,
         role, content, tool_calls, tool_name, created_at)
messages_fts(content, session_id, role)        -- FTS5 virtual table
meta(key PK, value)                             -- internal kv (e.g. last_prune)
```

- `messages_fts` is kept in sync by an `AFTER INSERT` trigger on `messages`, so full-text search
  is automatic.
- `title` is `UNIQUE` but **nullable** — see "Identifiers" below.
- `source` is the flow type (`ingest` / `query` / …); deleting a session cascades to its messages.

## Lifecycle: how a session gets saved

At the end of **every** streamed turn, `src/agents/stream.py:_save_session()` runs:

```python
session_id = save_session(conn, thread_id, messages, started_at, flow_type)  # metadata + messages
maybe_auto_title(conn, session_id, messages)                                 # title (async, best-effort)
```

So the catalog is rebuilt after each turn from the agent's full message list.

### Idempotent saves

`run_turn_stream_async` passes the **entire accumulated thread** every turn, so the same messages
are re-presented as the conversation grows. To avoid duplicate rows, `save_session`:

- `INSERT OR IGNORE` on the session row (keyed by `thread_id`), and
- gives each message a **deterministic id** — `_stable_message_id(thread_id, position, role,
  content)` — with `INSERT OR IGNORE`, so re-saving a thread is a no-op for rows already written
  (and the FTS index stays duplicate-free).

### Auto-titling is asynchronous and best-effort

`maybe_auto_title()` spawns a **daemon thread** that calls an LLM to generate a 3–7 word title,
then `set_session_title()` stores it (resolving collisions via lineage numbering, e.g.
`"my project #2"`). It only runs if the session has both a human and an AI message and no title
yet. Failures are logged and swallowed — **a session may have no title** (titling pending,
disabled, or errored).

## Identifiers: `thread_id` vs `title`

| | `thread_id` | `title` |
|---|---|---|
| Form | UUID7 string | short human phrase |
| Uniqueness | primary key | `UNIQUE` (collisions auto-numbered) |
| Always present? | **yes**, at creation | **no** — set later by a background thread, may be missing |
| Used to resume? | **yes** — required by the checkpointer / `create_supervisor` / sandbox | not directly; only as a lookup → `thread_id` |
| Stable? | yes | can change (lineage numbering) and arrives after the turn |

**Why resume is keyed on `thread_id`:** it's the value the resume machinery actually needs
(`checkpoints.db`, `create_supervisor(thread_id)`, the Daytona sandbox registration all use it),
and it always exists. A title is a convenience alias that maps *to* a `thread_id`
(`SELECT id FROM sessions WHERE title = ?`) but isn't guaranteed to exist and isn't what the
graph state is keyed on.

**In practice the CLI accepts *either*.** `session_manager.resolve_thread_id(conn, ref)` resolves a
reference to a `thread_id`:

1. exact `thread_id` → returned as-is;
2. a **specific** lineage member (`"name #N"`) → that exact title;
3. a **base** name → the most recent session across its lineage (`name` or `name #N`).

`paper2wiki sessions resume <ref>` calls this and resumes via the resolved `thread_id`. Tab-completion
on the argument surfaces both ids (with the title as description) and titles. Resume still runs on the
`thread_id` under the hood — the title is only an input alias.

## Pruning

Pruning is **manual and explicit** — there is no auto-prune (history is valuable for search
recall). One command cleans both DBs in lockstep, keyed by `thread_id`:

```bash
uv run python -m src.cli.app sessions prune                 # ended sessions > 90 days
uv run python -m src.cli.app sessions prune --older-than-days 30
uv run python -m src.cli.app sessions prune -y              # skip the confirm prompt
```

**What gets pruned:** sessions with `status='ended'` older than the threshold are deleted from
`sessions.db` (messages cascade), **and** their checkpoint state is evicted from `checkpoints.db`.

**What is kept:** active sessions are never pruned, regardless of age.

> A pruned session can no longer be resumed — both its history and its resumable graph state are
> gone. `prune` is the only `sessions` subcommand that loads the checkpointer; `ls` / `search` /
> `resume` stay agent-free and fast.


## Notes / gotchas

- **No auto-pruning** — see the "Pruning" section above; it's manual by design.
- The connection uses WAL mode and `foreign_keys = ON` (for cascade deletes).
- This package is independent of the agent at import time — querying `sessions.db` does **not**
  load the agent/tools graph, which is why the CLI's `sessions` commands are fast (except `prune`,
  which loads the checkpointer to evict — see above).
