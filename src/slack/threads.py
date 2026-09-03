"""Map a Slack thread to a LangGraph thread id.

``sessions.db``'s ``id`` column **is** the LangGraph ``thread_id`` (see
``src/sessions/session_manager.py:save_session``), so Slack needs no schema
change and no mapping table — the id is derived from the Slack thread itself.

Deriving it (rather than storing it) makes resuming free: the same Slack thread
always produces the same id, so ``checkpoints.db`` restores that conversation
with no extra persistence code.
"""

from __future__ import annotations

# Slack ids are already filesystem/SQL-safe (channel: "C0123ABC", ts:
# "1735689600.123456"), so no escaping is needed.
PREFIX = "slack"


def thread_id_for(channel: str, thread_ts: str | None, message_ts: str) -> str:
    """Return the LangGraph thread id for a Slack message.

    A message with no ``thread_ts`` is a new top-level message and starts a
    session, keyed on its own timestamp. A threaded reply carries the parent's
    ``thread_ts`` and therefore resumes that session.

    Args:
        channel:    Slack channel id the message arrived in.
        thread_ts:  Parent message ts when this is a threaded reply, else None.
        message_ts: This message's own ts.

    Returns:
        A stable id like ``slack-C0123ABC-1735689600.123456``.
    """
    return f"{PREFIX}-{channel}-{thread_ts or message_ts}"


def reply_ts_for(thread_ts: str | None, message_ts: str) -> str:
    """Return the ts to post replies under, so answers stay in one thread.

    Same rule as :func:`thread_id_for`: replies to a top-level message open a
    thread under it; replies inside a thread stay in that thread.
    """
    return thread_ts or message_ts
