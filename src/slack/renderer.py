"""``SlackRenderer`` — the Slack implementation of the ``Renderer`` protocol.

``src/agents/stream.py`` drives every front-end through the seven methods in
``src/agents/renderer.py``. ``RichRenderer`` is the terminal one; this is the
Slack one. The agent, streaming and persistence layers are untouched.

Two things worth knowing before reading:

**Token buffering.** ``on_token`` fires per streamed chunk, but Slack allows
roughly one message edit per second. Editing per chunk would hit the rate limit
inside one sentence, so text is buffered and flushed on a timer
(``edit_interval``), with a final flush on ``on_turn_end``.

**HITL waits without blocking.** The terminal can call ``input()``; Slack has to
post buttons and wait for a click that arrives later, as a *separate* event.
``handle_interrupts`` registers an ``asyncio.Future`` in ``_PENDING``, posts the
buttons, and awaits it — the event loop stays free, so the click can be received
and ``submit_decision`` can resolve the future. The decision shapes themselves
come from ``build_decisions`` — the same helper ``RichRenderer`` uses — so the
two front-ends cannot drift apart.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from src.agents.renderer import _AutoApprove, build_decisions

logger = logging.getLogger(__name__)

# token -> the future a waiting turn is parked on. Populated by
# handle_interrupts, resolved by submit_decision() when the click arrives.
_PENDING: dict[str, "asyncio.Future[tuple[str, str]]"] = {}

# Decisions Slack can express with buttons alone. "edit" and "respond" both need
# free-text *arguments*, which would mean a modal per tool schema — out of scope
# for now, so those tools fall back to approve/reject in Slack and keep their
# full option set in the terminal.
_BUTTON_CHOICES = ("a", "r", "yolo")

_BUTTON_LABEL = {"a": "Approve", "r": "Reject", "yolo": "Approve all"}
_BUTTON_STYLE = {"a": "primary", "r": "danger"}


def submit_decision(token: str, choice: str, reason: str = "") -> bool:
    """Hand a HITL decision back to the turn awaiting it in ``handle_interrupts``.

    Called when a button is clicked or a reject modal is submitted. Returns False
    if the token is unknown — a stale click on an old message, which should be
    ignored rather than raised.
    """
    future = _PENDING.get(token)
    if future is None or future.done():
        logger.debug("Ignoring decision for unknown/settled token %s", token)
        return False
    loop = future.get_loop()
    # call_soon_threadsafe so this is safe whether the click arrives on the
    # agent's loop or from another thread.
    loop.call_soon_threadsafe(_settle_future, future, choice, reason)
    return True


def _settle_future(future: "asyncio.Future", choice: str, reason: str) -> None:
    """Resolve a pending decision, ignoring a double-click that lost the race."""
    if not future.done():
        future.set_result((choice, reason))


def _truncate(text: str, *, max_lines: int, max_chars: int) -> str:
    """Shorten tool output for a preview, marking that it was cut."""
    text = (text or "").strip()
    lines = text.splitlines()
    clipped = "\n".join(lines[:max_lines])
    if len(clipped) > max_chars:
        clipped = clipped[:max_chars]
    if clipped != text:
        clipped += "\n… (truncated)"
    return clipped


class SlackRenderer:
    """Streams one agent turn into one Slack thread.

    Args:
        client:      Slack ``WebClient`` (or any object with the same
                     ``chat_postMessage`` / ``chat_update`` methods — the tests
                     pass a fake).
        channel:     Channel id to post in.
        thread_ts:   Parent ts, so every message lands in the same thread.
        auto_approve: Skip HITL prompts entirely (the ``yolo`` equivalent).
        debug:       Send ``on_debug`` messages to the logger.
        edit_interval: Seconds between streamed edits. Slack allows ~1/s.
        decision_timeout: How long to wait for a HITL click before giving up.
    """

    _PREVIEW_LINES = 6
    _PREVIEW_CHARS = 400

    def __init__(
        self,
        client: Any,
        channel: str,
        thread_ts: str,
        *,
        auto_approve: bool = False,
        debug: bool = False,
        edit_interval: float = 0.7,
        decision_timeout: float = 900.0,
    ) -> None:
        self.client = client
        self.channel = channel
        self.thread_ts = thread_ts
        self.debug = debug
        self.edit_interval = edit_interval
        self.decision_timeout = decision_timeout

        self._state = _AutoApprove(auto_approve)
        self._buffer = ""          # text streamed so far this segment
        self._sent = ""            # text Slack already has, to skip no-op edits
        self._ts: str | None = None  # ts of the message we are editing
        self._last_edit = 0.0
        # Reason captured from a reject modal, handed to build_decisions'
        # message_fn on the next call.
        self._pending_reason = ""

    @property
    def auto_approve(self) -> bool:
        return self._state.auto_approve

    # --- posting helpers ----------------------------------------------------

    def _post(self, text: str) -> str | None:
        """Post a new message in the thread; return its ts (None on failure)."""
        try:
            resp = self.client.chat_postMessage(
                channel=self.channel, thread_ts=self.thread_ts, text=text,
            )
            return resp.get("ts")
        except Exception:
            # Slack being unavailable must not kill the agent turn — the work is
            # already done and persisted, only the display fails.
            logger.exception("Slack post failed")
            return None

    def notice(self, text: str) -> None:
        """Post a one-off message (errors, status) outside the streamed text."""
        self._post(text)

    def _flush(self, force: bool = False) -> None:
        """Write the buffer to Slack, rate-limited unless ``force``."""
        if not self._buffer:
            return
        if self._buffer == self._sent:
            return  # nothing new since the last write — skip the API call
        now = time.monotonic()
        if not force and (now - self._last_edit) < self.edit_interval:
            return
        self._last_edit = now
        self._sent = self._buffer

        if self._ts is None:
            self._ts = self._post(self._buffer)
            return
        try:
            self.client.chat_update(
                channel=self.channel, ts=self._ts, text=self._buffer,
            )
        except Exception:
            logger.exception("Slack update failed")

    # --- Renderer protocol --------------------------------------------------

    def on_turn_start(self) -> None:
        """Begin a fresh streamed segment.

        Called once per turn *and* again after each HITL resume, so each segment
        gets its own message rather than editing the pre-approval text.
        """
        self._buffer = ""
        self._sent = ""
        self._ts = None
        self._last_edit = 0.0

    def on_token(self, text: str) -> None:
        """Buffer streamed text; edit Slack at most once per ``edit_interval``."""
        self._buffer += text
        self._flush()

    def on_tool_call(self, name: str, args: Any) -> None:
        """Announce a tool call — in Slack this is the only progress signal."""
        self._flush(force=True)
        self._post(f"🔧 `{name}`  `{str(args)[:120]}`")

    def on_tool_result(self, name: str, content: str) -> None:
        """Post a truncated preview of the tool's output."""
        preview = _truncate(
            content, max_lines=self._PREVIEW_LINES, max_chars=self._PREVIEW_CHARS,
        )
        if preview:
            self._post(f"↳ `{name}`\n```\n{preview}\n```")

    def on_turn_end(self) -> None:
        """Commit whatever text is left in the buffer."""
        self._flush(force=True)

    def on_debug(self, message: str) -> None:
        """Diagnostics go to the log, never to the channel."""
        if self.debug:
            logger.debug(message)

    # --- HITL ---------------------------------------------------------------

    async def handle_interrupts(self, interrupts: list) -> list[dict]:
        """Post approve/reject buttons and await the click.

        Delegates the decision *shapes* to ``build_decisions`` so they stay
        identical to the terminal front-end; this method only supplies the three
        callbacks that decide how a human is asked.
        """
        self._flush(force=True)

        async def prompt_fn(action, review_config, choices):
            offered = [c for c in choices if c in _BUTTON_CHOICES]
            if not offered:
                # Tool allows only edit/respond, which need typed arguments.
                # Refusing is the safe default; the terminal can still do it.
                self._post(
                    f"⚠️ `{action['name']}` needs an edited/typed reply, which "
                    f"Slack can't collect — rejecting. Use the terminal for this one."
                )
                return "r"

            token = uuid.uuid4().hex
            waiter: "asyncio.Future[tuple[str, str]]" = (
                asyncio.get_running_loop().create_future()
            )
            _PENDING[token] = waiter
            try:
                self._post_approval_request(action, offered, token)
                try:
                    choice, reason = await asyncio.wait_for(
                        waiter, timeout=self.decision_timeout
                    )
                except asyncio.TimeoutError:
                    self._post("⏰ No response — rejecting to be safe.")
                    return "r"
                self._pending_reason = reason
                return choice
            finally:
                _PENDING.pop(token, None)

        async def edit_fn(action):
            # Unreachable: "e" is filtered out of `offered` above. Present so the
            # build_decisions contract is satisfied.
            return ""

        async def message_fn(action, kind):
            reason, self._pending_reason = self._pending_reason, ""
            return reason

        return await build_decisions(
            interrupts, prompt_fn, edit_fn, message_fn, self._state
        )

    def _post_approval_request(self, action: dict, offered: list[str], token: str) -> None:
        """Render the Block Kit approval card for one pending tool call."""
        args_preview = _truncate(str(action.get("args", "")), max_lines=8, max_chars=600)
        blocks = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"🔔 *Approval required*\n*Tool:* `{action['name']}`",
                },
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"```\n{args_preview}\n```"},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": _BUTTON_LABEL[c]},
                        "action_id": f"hitl:{token}:{c}",
                        "value": c,
                        **({"style": _BUTTON_STYLE[c]} if c in _BUTTON_STYLE else {}),
                    }
                    for c in offered
                ],
            },
        ]
        try:
            self.client.chat_postMessage(
                channel=self.channel,
                thread_ts=self.thread_ts,
                text=f"Approval required: {action['name']}",  # notification fallback
                blocks=blocks,
            )
        except Exception:
            logger.exception("Slack approval post failed")
