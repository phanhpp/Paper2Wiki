"""Async lifecycle wrapper for CLI commands.

The agent owns thread-scoped async resources — an ``AsyncSqliteSaver`` checkpointer and
(outside eval mode) a Daytona sandbox. Every command runs its coroutine through ``run_async``,
which guarantees the checkpointer and the sessions DB connection are closed on the way out,
even on ``KeyboardInterrupt``.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, TypeVar

T = TypeVar("T")


def run_async(coro: Awaitable[T]) -> T | None:
    """Run ``coro`` to completion, then tear down agent/session resources.

    The checkpointer (``close_checkpointer``) is async and must be closed inside the event
    loop; the sessions connection (``close_sessions_conn``) is sync and closed afterwards.
    A Ctrl-C during the run is swallowed so the CLI exits cleanly rather than dumping a
    traceback.
    """
    # Imported lazily so `paper2wiki --help` (and env-setup in command bodies) don't pull
    # in the agent/tools import graph before flags have configured the environment.
    from src.agents.agent import close_checkpointer
    from src.sessions.sessions_db_setup import close_sessions_conn

    async def _wrapped() -> T:
        try:
            return await coro
        finally:
            await close_checkpointer()

    try:
        return asyncio.run(_wrapped())
    except KeyboardInterrupt:
        return None
    finally:
        close_sessions_conn()
