"""Socket Mode listener — the Slack entry point for the agent (Loop 3).

Why Socket Mode: this runs on a laptop with no public URL, so Slack can't POST
to us. Socket Mode inverts the direction — the app dials *out* over a websocket,
so no webhook, no inbound port, no cron. (A deployed variant would use the
Events API instead; see ``docs/loop3_slack.md``.)

Everything runs on **one event loop**, no threads::

    AsyncSocketModeHandler   receives Slack events
        │
        ├─ message  ──►  work queue  ──►  worker task   ONE TURN AT A TIME
        │                                     │
        │                                     └─ run_turn_stream_async(...)
        │                                            └─ await handle_interrupts()
        │                                                  parks on a Future
        └─ button click ─────────────────────────────────► resolves that Future

Turns are serialised through the queue for two reasons: ``checkpoints.db`` and
``sessions.db`` are single-writer SQLite, and Loop 2's before/after wiki snapshot
diff (``src/middleware/wiki_rubric.py``) would attribute a second run's writes to
the first if they overlapped.

The approval wait does **not** block the loop — ``handle_interrupts`` awaits a
Future — so the click that resolves it can be received while a turn is parked.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

from src.slack.renderer import SlackRenderer, step_log, submit_decision
from src.slack.threads import reply_ts_for, thread_id_for

logger = logging.getLogger(__name__)

_REJECT_MODAL = "hitl_reject"
# action_id is "hitl:<token>:<choice>" — see SlackRenderer._post_approval_request.
_HITL_ACTION = re.compile(r"^hitl:")
# action_id is "steps:<token>" — the "View details" button on the status line.
_STEPS_ACTION = re.compile(r"^steps:")


def build_app(
    *,
    bot_token: str,
    channel_id: str,
    eval_mode: bool = False,
    auto_approve: bool = False,
    debug: bool = False,
):
    """Build the Bolt app with all handlers registered.

    Kept separate from :func:`serve` so the wiring can be inspected without
    opening a websocket. Nothing here touches the network — ``AsyncApp`` does not
    verify the token at construction, so :func:`check_credentials` does it
    explicitly instead.
    """
    from slack_bolt.async_app import AsyncApp
    from slack_sdk import WebClient

    app = AsyncApp(token=bot_token)

    # The renderer needs a *sync* client: six of the seven Renderer methods are
    # sync and cannot await. Each call blocks the loop for one HTTP round trip,
    # which is fine — turns are serialised, so nothing else is waiting to run.
    post_client = WebClient(token=bot_token)

    work: "asyncio.Queue[Any]" = asyncio.Queue()
    # One supervisor per Slack thread, reused across follow-up messages. Building
    # it provisions a thread-scoped Daytona sandbox, so rebuilding per message
    # would add tens of seconds to every reply.
    supervisors: dict[str, Any] = {}

    async def _worker() -> None:
        """Drain the queue one job at a time, forever."""
        while True:
            job = await work.get()
            try:
                await job()
            except Exception:
                logger.exception("Agent turn failed")
            finally:
                work.task_done()

    @app.event("message")
    async def on_message(event: dict, client) -> None:
        # Ignore our own posts, edits/deletions, and anything outside the one
        # configured channel — a stray @mention elsewhere must not start a real
        # ingest.
        if event.get("channel") != channel_id:
            return
        if event.get("bot_id") or event.get("subtype"):
            return
        text = (event.get("text") or "").strip()
        if not text:
            return

        channel = event["channel"]
        message_ts = event["ts"]
        thread_ts = event.get("thread_ts")
        tid = thread_id_for(channel, thread_ts, message_ts)
        reply_ts = reply_ts_for(thread_ts, message_ts)

        # Immediate ack. The first marp request in a thread waits tens of seconds
        # for a Daytona sandbox to cold-start, and silence looks like a hang.
        status_ts = None
        try:
            ack = await client.chat_postMessage(
                channel=channel, thread_ts=reply_ts, text="🤔 On it…",
            )
            status_ts = ack.get("ts")
        except Exception:
            logger.exception("Slack ack failed")

        # The renderer takes this message over as a live status line: it shows the
        # current tool while the turn runs, then settles into a step summary.
        renderer = SlackRenderer(
            post_client, channel, reply_ts,
            status_ts=status_ts, auto_approve=auto_approve, debug=debug,
        )

        async def _turn() -> None:
            from src.agents.agent import create_supervisor
            from src.agents.stream import run_turn_stream_async

            agent = supervisors.get(tid)
            if agent is None:
                agent = await create_supervisor(tid, eval_mode=eval_mode)
                supervisors[tid] = agent
            try:
                await run_turn_stream_async(
                    text, agent=agent, thread_id=tid, renderer=renderer,
                )
            except Exception:
                logger.exception("Turn failed for thread %s", tid)
                renderer.notice("💥 That turn failed — see the terminal log.")

        await work.put(_turn)

    @app.action(_HITL_ACTION)
    async def on_hitl_button(ack, body: dict, client) -> None:
        """Approve/yolo resolve immediately; reject opens a modal for a reason."""
        await ack()
        action = body["actions"][0]
        _, token, choice = action["action_id"].split(":", 2)

        if choice == "r":
            try:
                await client.views_open(
                    trigger_id=body["trigger_id"],
                    view={
                        "type": "modal",
                        "callback_id": _REJECT_MODAL,
                        "private_metadata": token,
                        "notify_on_close": True,  # so a dismissed modal still answers
                        "title": {"type": "plain_text", "text": "Reject"},
                        "submit": {"type": "plain_text", "text": "Send"},
                        "blocks": [{
                            "type": "input",
                            "block_id": "reason",
                            "optional": True,
                            "label": {"type": "plain_text", "text": "Why? (optional)"},
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "text",
                                "multiline": True,
                            },
                        }],
                    },
                )
            except Exception:
                # Couldn't open the modal — still reject, just without a reason.
                logger.exception("Reject modal failed; rejecting without a reason")
                submit_decision(token, "r", "")
        else:
            submit_decision(token, choice, "")

        await _settle(client, body, choice)

    @app.action(_STEPS_ACTION)
    async def on_view_steps(ack, body: dict, client) -> None:
        """Open a modal with this turn's tool calls and their output.

        Slack has no collapsible section, so "expand" means a modal.
        """
        await ack()
        token = body["actions"][0]["action_id"].split(":", 1)[1]
        text = step_log(token) or "That step log has expired."
        try:
            await client.views_open(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "title": {"type": "plain_text", "text": "Steps"},
                    "close": {"type": "plain_text", "text": "Close"},
                    "blocks": [{
                        "type": "section",
                        # Slack caps a text block at 3000 chars.
                        "text": {"type": "mrkdwn", "text": text[:2900]},
                    }],
                },
            )
        except Exception:
            logger.exception("Could not open the steps modal")

    @app.view(_REJECT_MODAL)
    async def on_reject_submit(ack, view: dict) -> None:
        await ack()
        values = view.get("state", {}).get("values", {})
        reason = (values.get("reason", {}).get("text", {}).get("value") or "").strip()
        submit_decision(view["private_metadata"], "r", reason)

    @app.view_closed(_REJECT_MODAL)
    async def on_reject_dismissed(ack, view: dict) -> None:
        """Dismissing the modal is still a reject — never leave the turn hanging."""
        await ack()
        submit_decision(view["private_metadata"], "r", "")

    # build_app is pure wiring — it starts nothing. serve() owns the runtime, so
    # tests can deliver an event and assert it was queued without a turn firing.
    app.work_queue = work
    app.run_worker = _worker
    return app


def check_credentials(bot_token: str, app_token: str) -> str:
    """Verify both tokens before opening the websocket; return the bot name.

    ``AsyncApp`` (unlike the sync ``App``) does not call ``auth.test`` at start-up,
    and Socket Mode retries connection failures forever — so a misconfigured token
    otherwise scrolls tracebacks instead of saying what is wrong. Both are checked
    here so the problem is named once, up front.
    """
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    client = WebClient(token=bot_token)
    try:
        resp = client.auth_test()
    except SlackApiError as exc:
        raise RuntimeError(
            f"SLACK_BOT_TOKEN rejected by Slack ({exc.response.get('error')}). "
            "It should start 'xoxb-' and comes from OAuth & Permissions, after "
            "installing the app. See docs/slack_setup.md."
        ) from exc

    # apps.connections.open is the only way to prove the app-level token works.
    # It just hands back a websocket URL, which we discard — connecting is the
    # handler's job.
    try:
        WebClient(token=app_token).apps_connections_open(app_token=app_token)
    except SlackApiError as exc:
        error = exc.response.get("error")
        if error == "missing_scope":
            raise RuntimeError(
                "SLACK_APP_TOKEN is missing the 'connections:write' scope "
                f"(it has: {exc.response.get('provided')}).\n"
                "App-level token scopes cannot be edited — generate a new one:\n"
                "  Basic Information -> App-Level Tokens -> Generate Token and Scopes\n"
                "  add 'connections:write', then put the new xapp-... in .env"
            ) from exc
        raise RuntimeError(
            f"SLACK_APP_TOKEN rejected by Slack ({error}). It should start "
            "'xapp-' and comes from Basic Information -> App-Level Tokens. "
            "See docs/slack_setup.md."
        ) from exc

    return resp.get("user", "the bot")


async def _settle(client, body: dict, choice: str) -> None:
    """Replace the approval card with its outcome so buttons can't be re-clicked."""
    label = {"a": "✅ Approved", "r": "🚫 Rejected", "yolo": "✅ Approved (all)"}
    try:
        await client.chat_update(
            channel=body["channel"]["id"],
            ts=body["message"]["ts"],
            text=label.get(choice, "Decided"),
            blocks=[{
                "type": "section",
                "text": {"type": "mrkdwn", "text": label.get(choice, "Decided")},
            }],
        )
    except Exception:
        logger.exception("Could not update the approval message")


def serve(
    *,
    channel_id: str | None = None,
    eval_mode: bool = False,
    auto_approve: bool = False,
    debug: bool = False,
) -> None:
    """Open the Socket Mode connection and listen until interrupted."""
    bot_token = os.environ["SLACK_BOT_TOKEN"]
    app_token = os.environ["SLACK_APP_TOKEN"]
    channel = channel_id or os.environ["SLACK_CHANNEL_ID"]

    bot_name = check_credentials(bot_token, app_token)
    print(f"Connected as {bot_name} — listening on {channel}. Ctrl-C to stop.")

    async def _run() -> None:
        from slack_bolt.adapter.socket_mode.aiohttp import AsyncSocketModeHandler

        app = build_app(
            bot_token=bot_token,
            channel_id=channel,
            eval_mode=eval_mode,
            auto_approve=auto_approve,
            debug=debug,
        )
        logger.info("Listening on Slack channel %s", channel)
        # The worker drains queued turns; the handler feeds it. Both live on this
        # one loop, which is what lets an approval wait park without blocking.
        asyncio.create_task(app.run_worker())
        await AsyncSocketModeHandler(app, app_token).start_async()

    asyncio.run(_run())
