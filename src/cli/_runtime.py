"""Starts and stops the event loop for commands that run the agent.

Running the agent opens two database connections, and both have to be closed again
afterwards — otherwise files stay locked and writes can be lost. Each has to be closed
a different way, and the order matters.

Rather than repeat that in every command (and eventually forget it somewhere), there is
one wrapper. Any command that runs the agent goes through ``run_async``.

Functions:
    run_async(call) — run one async call, then close both databases, in the right
                      order, even if the run fails. Ctrl-C exits quietly.
"""

from __future__ import annotations

import asyncio
from typing import Awaitable, TypeVar

T = TypeVar("T")


def run_async(coro: Awaitable[T]) -> T | None:
    """Execute an async task inside a managed event loop and safely close databases.

    Runs an un-awaited coroutine to completion while ensuring both the async
    checkpointer database and the sync sessions database are closed in the correct
    order upon exit, failure, or cancellation.

    Args:
        coro: The un-awaited coroutine to run (e.g., created by calling an `async def`
            function without `await`).

    Returns:
        The return value of the coroutine, or `None` if interrupted by Ctrl+C.

    Database Cleanup Order:
        1. Async Checkpointer: Closed inside the active event loop via
           `_runner_with_cleanup()`.
        2. Sync Sessions DB: Closed after the event loop stops via `close_sessions_conn()`.

    Both close even if the run fails or you press Ctrl-C. Ctrl-C exits quietly rather
    than printing a traceback.
    """
    # Imported lazily so `paper2wiki --help` (and env-setup in command bodies) don't pull
    # in the agent/tools import graph before flags have configured the environment.
    from src.agents.agent import close_checkpointer
    from src.sessions.sessions_db_setup import close_sessions_conn

    async def _runner_with_cleanup() -> T:
        try:
            return await coro
        finally:
            await close_checkpointer()

    try:
        return asyncio.run(_runner_with_cleanup())
    except KeyboardInterrupt:
        return None
    finally:
        close_sessions_conn()
