"""Unit tests for the Slack front-end (Loop 3).

No network and no workspace: the renderer talks to a ``FakeSlackClient`` that
records calls instead of making them, and ``build_app`` is built with token
verification off so Bolt never calls ``auth.test``.

What these cover is the *logic* — thread-id derivation, token buffering, HITL
decision shapes, the channel filter. What they cannot cover is whether your
tokens are valid, your scopes are right, or the bot was invited to the channel;
that needs a live run (see ``src/slack/README.md`` → Verification).
"""

from __future__ import annotations

import asyncio

import pytest

from src.slack.renderer import SlackRenderer, submit_decision
from src.slack.threads import reply_ts_for, thread_id_for


class FakeSlackClient:
    """Records Slack API calls instead of making them."""

    def __init__(self) -> None:
        self.posted: list[dict] = []
        self.updated: list[dict] = []
        self._n = 0

    def chat_postMessage(self, **kwargs):
        self._n += 1
        self.posted.append(kwargs)
        return {"ts": f"ts{self._n}"}

    def chat_update(self, **kwargs):
        self.updated.append(kwargs)
        return {"ok": True}

    @property
    def texts(self) -> list[str]:
        return [c.get("text", "") for c in self.posted]


class AsyncFakeSlackClient(FakeSlackClient):
    """Bolt hands handlers an *async* client; the renderer gets the sync one."""

    async def chat_postMessage(self, **kwargs):  # type: ignore[override]
        return super().chat_postMessage(**kwargs)

    async def chat_update(self, **kwargs):  # type: ignore[override]
        return super().chat_update(**kwargs)


def _interrupt(name: str = "write_file", allowed=("approve", "reject")):
    """One interrupt in the shape stream.py passes to handle_interrupts."""
    class _I:
        value = {
            "action_requests": [{"name": name, "args": {"file_path": "wiki/x.md"}}],
            "review_configs": [{"action_name": name, "allowed_decisions": list(allowed)}],
        }
    return [_I()]


async def _click_soon(choice: str, reason: str = "") -> None:
    """Simulate a button click arriving while a turn is parked on its Future.

    Scheduled as a task *before* awaiting handle_interrupts: the await yields
    control, this runs, and the Future resolves. That interleaving is the point
    — it only works because the approval wait doesn't block the loop.
    """
    from src.slack.renderer import _PENDING

    for _ in range(200):
        await asyncio.sleep(0.005)
        if _PENDING:
            submit_decision(next(iter(_PENDING)), choice, reason)
            return


# --- threads.py -------------------------------------------------------------

@pytest.mark.unit
def test_thread_id_is_stable_for_the_same_slack_thread():
    """A reply carries the parent's ts, so it resumes the same session."""
    parent = thread_id_for("C1", None, "111.1")
    reply = thread_id_for("C1", "111.1", "222.2")
    assert parent == reply == "slack-C1-111.1"


@pytest.mark.unit
def test_thread_id_differs_per_channel_and_per_new_message():
    assert thread_id_for("C1", None, "111.1") != thread_id_for("C2", None, "111.1")
    assert thread_id_for("C1", None, "111.1") != thread_id_for("C1", None, "222.2")


@pytest.mark.unit
def test_reply_ts_keeps_answers_in_one_thread():
    assert reply_ts_for(None, "111.1") == "111.1"    # opens a thread under it
    assert reply_ts_for("111.1", "222.2") == "111.1"  # stays in the thread


# --- streaming --------------------------------------------------------------

@pytest.mark.unit
def test_on_token_buffers_instead_of_editing_per_token():
    """A burst of tokens must not become a burst of Slack edits."""
    fake = FakeSlackClient()
    r = SlackRenderer(fake, "C1", "111.1")  # default 0.7s interval
    r.on_turn_start()
    for word in ["hello ", "wonderful ", "streaming ", "world"]:
        r.on_token(word)
    r.on_turn_end()

    # One post (first token, shown immediately) + one final edit — not four.
    assert fake.texts == ["hello "]
    assert [u["text"] for u in fake.updated] == ["hello wonderful streaming world"]


@pytest.mark.unit
def test_turn_end_does_not_repeat_an_unchanged_edit():
    """Flushing twice with no new tokens must not spend a second API call."""
    fake = FakeSlackClient()
    r = SlackRenderer(fake, "C1", "111.1", edit_interval=0.0)
    r.on_turn_start()
    r.on_token("a")
    r.on_token("b")
    r.on_turn_end()
    r.on_turn_end()
    assert [u["text"] for u in fake.updated] == ["ab"]


@pytest.mark.unit
def test_each_segment_gets_its_own_message():
    """After a HITL resume, on_turn_start begins a new message."""
    fake = FakeSlackClient()
    r = SlackRenderer(fake, "C1", "111.1", edit_interval=0.0)
    r.on_turn_start(); r.on_token("before"); r.on_turn_end()
    r.on_turn_start(); r.on_token("after"); r.on_turn_end()
    assert fake.texts == ["before", "after"]


@pytest.mark.unit
def test_tool_activity_updates_the_status_line_instead_of_posting():
    """One message that changes, not one message per tool call."""
    fake = FakeSlackClient()
    r = SlackRenderer(fake, "C1", "111.1", status_ts="status1")
    r.on_turn_start()
    r.on_tool_call("grep", {"pattern": "attention"})
    r.on_tool_result("grep", "line\n" * 50)
    r.on_tool_call("read_file", {"file_path": "wiki/index.md"})

    assert fake.posted == []                       # nothing new in the channel
    assert all(u["ts"] == "status1" for u in fake.updated)
    assert "grep" in fake.updated[0]["text"]
    assert "read_file" in fake.updated[-1]["text"]


@pytest.mark.unit
def test_status_settles_into_a_summary_with_a_details_button():
    fake = FakeSlackClient()
    r = SlackRenderer(fake, "C1", "111.1", status_ts="status1")
    r.on_turn_start()
    r.on_tool_call("grep", {"pattern": "x"})
    r.on_tool_result("grep", "a result")
    r.on_token("here is the answer")
    r.on_turn_end()

    summary = [u for u in fake.updated if u["ts"] == "status1"][-1]
    assert "1 step" in summary["text"]
    button = summary["blocks"][0]["accessory"]
    assert button["text"]["text"] == "View details"

    from src.slack.renderer import step_log
    log = step_log(button["action_id"].split(":", 1)[1])
    assert "grep" in log and "a result" in log


@pytest.mark.unit
def test_step_log_is_capped():
    """A long-running `serve` must not accumulate step logs forever."""
    from src.slack.renderer import _MAX_STEP_LOGS, _STEP_LOGS, _remember_steps

    _STEP_LOGS.clear()
    for i in range(_MAX_STEP_LOGS + 10):
        _remember_steps(f"t{i}", f"log {i}")
    assert len(_STEP_LOGS) == _MAX_STEP_LOGS
    assert "t0" not in _STEP_LOGS          # oldest evicted
    assert f"t{_MAX_STEP_LOGS + 9}" in _STEP_LOGS


@pytest.mark.unit
def test_slack_failures_do_not_kill_the_turn():
    """The work is already done and persisted; only the display fails."""
    class Broken:
        def chat_postMessage(self, **kw):
            raise RuntimeError("slack down")
        def chat_update(self, **kw):
            raise RuntimeError("slack down")

    r = SlackRenderer(Broken(), "C1", "111.1", edit_interval=0.0)
    r.on_turn_start()
    r.on_token("hi")
    r.on_turn_end()  # must not raise


# --- HITL -------------------------------------------------------------------

@pytest.mark.unit
async def test_approve_produces_the_same_shape_as_the_terminal():
    fake = FakeSlackClient()
    r = SlackRenderer(fake, "C1", "111.1")
    asyncio.create_task(_click_soon("a"))
    assert await r.handle_interrupts(_interrupt()) == [{"type": "approve"}]


@pytest.mark.unit
async def test_reject_with_reason_carries_the_message():
    """Reject + reason is feedback to the model — the tool is NOT run."""
    fake = FakeSlackClient()
    r = SlackRenderer(fake, "C1", "111.1")
    asyncio.create_task(_click_soon("r", "wrong directory"))
    assert await r.handle_interrupts(_interrupt()) == [
        {"type": "reject", "message": "wrong directory"}
    ]


@pytest.mark.unit
async def test_reject_without_reason_omits_the_message_key():
    fake = FakeSlackClient()
    r = SlackRenderer(fake, "C1", "111.1")
    asyncio.create_task(_click_soon("r", ""))
    assert await r.handle_interrupts(_interrupt()) == [{"type": "reject"}]


@pytest.mark.unit
async def test_yolo_sticks_for_the_rest_of_the_session():
    fake = FakeSlackClient()
    r = SlackRenderer(fake, "C1", "111.1")
    asyncio.create_task(_click_soon("yolo"))
    assert await r.handle_interrupts(_interrupt()) == [{"type": "approve"}]
    assert r.auto_approve
    # Second interrupt: no click simulated — auto-approve must short-circuit.
    assert await r.handle_interrupts(_interrupt()) == [{"type": "approve"}]


@pytest.mark.unit
async def test_auto_approve_never_posts_buttons():
    fake = FakeSlackClient()
    r = SlackRenderer(fake, "C1", "111.1", auto_approve=True)
    assert await r.handle_interrupts(_interrupt()) == [{"type": "approve"}]
    assert fake.posted == []


@pytest.mark.unit
async def test_timeout_rejects_rather_than_approving():
    """Silence must never be read as consent."""
    fake = FakeSlackClient()
    r = SlackRenderer(fake, "C1", "111.1", decision_timeout=0.05)
    assert await r.handle_interrupts(_interrupt()) == [{"type": "reject"}]


@pytest.mark.unit
async def test_buttons_offered_match_the_tools_allowed_decisions():
    fake = FakeSlackClient()
    r = SlackRenderer(fake, "C1", "111.1")
    asyncio.create_task(_click_soon("a"))
    await r.handle_interrupts(_interrupt(allowed=("approve",)))

    card = next(c for c in fake.posted if "blocks" in c)
    ids = [e["action_id"].rsplit(":", 1)[-1] for e in card["blocks"][-1]["elements"]]
    assert ids == ["a", "yolo"]      # reject not allowed by this tool → not offered


@pytest.mark.unit
async def test_edit_only_tool_is_rejected_not_silently_approved():
    """Slack can't collect typed args; refusing is the safe default."""
    fake = FakeSlackClient()
    r = SlackRenderer(fake, "C1", "111.1")
    assert await r.handle_interrupts(_interrupt(allowed=("edit",))) == [{"type": "reject"}]
    assert any("terminal" in t for t in fake.texts)


@pytest.mark.unit
def test_stale_click_is_ignored():
    assert submit_decision("no-such-token", "a") is False


# --- app wiring -------------------------------------------------------------

@pytest.mark.unit
async def test_build_app_registers_every_handler():
    from src.slack.app import build_app

    app = build_app(bot_token="xoxb-fake", channel_id="C1")
    # message + hitl button + steps button + modal submit + modal dismiss
    assert len(app._async_listeners) == 5


async def _deliver(app, event, client):
    """Invoke the registered message handler the way Bolt would."""
    await app._async_listeners[0].ack_function(event=event, client=client)


@pytest.mark.unit
async def test_a_real_message_enqueues_work_and_acks_immediately():
    """The positive case — without it the 'ignored' tests could pass vacuously.

    The immediate ack matters: the first marp request in a thread waits tens of
    seconds for a Daytona sandbox, and silence looks like a hang.
    """
    from src.slack.app import build_app

    app = build_app(bot_token="xoxb-fake", channel_id="C-allowed")
    client = AsyncFakeSlackClient()

    await _deliver(app, {"channel": "C-allowed", "ts": "1.1", "text": "ingest this"}, client)

    assert app.work_queue.qsize() == 1
    assert client.posted[0]["thread_ts"] == "1.1"


@pytest.mark.unit
async def test_messages_from_other_channels_are_ignored():
    """A stray message elsewhere in the workspace must not start a real ingest."""
    from src.slack.app import build_app

    app = build_app(bot_token="xoxb-fake", channel_id="C-allowed")
    client = AsyncFakeSlackClient()

    await _deliver(app, {"channel": "C-other", "ts": "1.1", "text": "ingest this"}, client)

    assert app.work_queue.qsize() == 0
    assert client.posted == []


@pytest.mark.unit
@pytest.mark.parametrize("event", [
    {"channel": "C-allowed", "ts": "1.1", "text": "hi", "bot_id": "B1"},   # our own post
    {"channel": "C-allowed", "ts": "1.1", "text": "hi", "subtype": "message_changed"},
    {"channel": "C-allowed", "ts": "1.1", "text": "   "},                  # empty
])
async def test_non_user_messages_are_ignored(event):
    from src.slack.app import build_app

    app = build_app(bot_token="xoxb-fake", channel_id="C-allowed")
    client = AsyncFakeSlackClient()

    await _deliver(app, event, client)

    assert app.work_queue.qsize() == 0


@pytest.mark.unit
async def test_turns_run_one_at_a_time():
    """Two queued turns must not overlap.

    Both SQLite DBs are single-writer, and Loop 2 works out what a run wrote by
    diffing the wiki before and after — an overlapping run would be blamed for
    files it never touched.
    """
    from src.slack.app import build_app

    app = build_app(bot_token="xoxb-fake", channel_id="C-allowed")
    running, overlapped = 0, False

    async def _job():
        nonlocal running, overlapped
        running += 1
        if running > 1:
            overlapped = True
        await asyncio.sleep(0.02)   # a turn that takes real time
        running -= 1

    await app.work_queue.put(_job)
    await app.work_queue.put(_job)

    worker = asyncio.create_task(app.run_worker())
    await app.work_queue.join()
    worker.cancel()

    assert not overlapped


@pytest.mark.unit
async def test_a_failing_turn_does_not_kill_the_worker():
    """One bad turn must not silently stop every later message."""
    from src.slack.app import build_app

    app = build_app(bot_token="xoxb-fake", channel_id="C-allowed")
    ran = []

    async def _boom():
        raise RuntimeError("turn exploded")

    async def _ok():
        ran.append("ok")

    await app.work_queue.put(_boom)
    await app.work_queue.put(_ok)

    worker = asyncio.create_task(app.run_worker())
    await app.work_queue.join()
    worker.cancel()

    assert ran == ["ok"]
