"""HITL interrupts must be prompted exactly once each.

The bug this pins: the approval box appeared **twice for one tool call**, with
byte-identical arguments. ``run_turn_stream_async`` used to detect interrupts from the
``values`` stream, which emits the whole state after every super-step — so when the loop
restarted to resume, the first snapshot still carried the interrupt that had just been
resolved, and it was handled again.

Deduplicating on ``Interrupt.id`` is *not* a valid fix, and the second test here is what
stops someone trying it: the id is ``xxh3_128_hexdigest(checkpoint_ns)`` — a hash of the
node position, not of the individual interrupt. Two genuine approvals at the same node
carry the same id, so skipping repeats would silently auto-approve the second one.

No network, no LLM, no DB — the agent is faked.
"""

from __future__ import annotations

import pytest

import src.agents.stream as stream_mod


class _Interrupt:
    """Stands in for ``langgraph.types.Interrupt``."""

    def __init__(self, value, id="same-node-hash"):
        self.value, self.id = value, id


class _State:
    def __init__(self, interrupts=()):
        self.values = {"messages": []}
        self.interrupts = tuple(interrupts)


class _ReplayingAgent:
    """Reproduces the bug: the stream *always* replays a stale interrupt.

    ``aget_state`` is the honest source — it reports the interrupt only until it is
    resolved. A correct implementation follows the state and prompts once.
    """

    def __init__(self, states):
        self._states = list(states)
        self.astream_calls = 0

    async def astream(self, payload, *args, **kwargs):
        self.astream_calls += 1
        # Every pass re-emits an interrupt on the values stream — the replay that used
        # to be mistaken for a second approval request.
        yield {"type": "values", "interrupts": (_Interrupt({"action_requests": []}),)}

    async def aget_state(self, config):
        return self._states.pop(0) if self._states else _State()


class _SpyRenderer:
    def __init__(self):
        self.handled: list = []

    def on_turn_start(self): ...
    def on_token(self, text): ...
    def on_tool_call(self, name, args): ...
    def on_tool_result(self, name, content): ...
    def on_turn_end(self): ...
    def on_debug(self, message): ...

    async def handle_interrupts(self, interrupts):
        self.handled.append(interrupts)
        return [{"type": "approve"}]


@pytest.mark.unit
async def test_one_interrupt_prompts_once_despite_stream_replay():
    """The regression: identical approval box appeared twice for a single tool call."""
    agent = _ReplayingAgent([
        _State([_Interrupt({"action_requests": [{"name": "edit_file"}]})]),  # pending
        _State(),                                                            # resolved
    ])
    renderer = _SpyRenderer()

    await stream_mod.run_turn_stream_async(
        "hi", agent=agent, thread_id="t-replay", renderer=renderer, persist=False,
    )

    assert len(renderer.handled) == 1, (
        f"prompted {len(renderer.handled)}× for one interrupt — the stream replay is "
        "being mistaken for a new approval request again"
    )
    assert agent.astream_calls == 2, "should stream once, then once more to resume"


@pytest.mark.unit
async def test_two_real_interrupts_sharing_an_id_both_prompt():
    """The safety net against 'fixing' this by deduplicating on Interrupt.id.

    Both interrupts carry the same id because the id hashes the node position. Skipping
    the second would approve a tool call the user never saw.
    """
    same_id = "xxh3-of-the-same-node"
    agent = _ReplayingAgent([
        _State([_Interrupt({"action_requests": [{"name": "execute"}]}, id=same_id)]),
        _State([_Interrupt({"action_requests": [{"name": "execute"}]}, id=same_id)]),
        _State(),
    ])
    renderer = _SpyRenderer()

    await stream_mod.run_turn_stream_async(
        "hi", agent=agent, thread_id="t-two", renderer=renderer, persist=False,
    )

    assert len(renderer.handled) == 2, (
        "two genuine approvals were collapsed into one — a tool ran without being shown"
    )


@pytest.mark.unit
async def test_no_interrupt_prompts_not_at_all():
    agent = _ReplayingAgent([_State()])
    renderer = _SpyRenderer()

    await stream_mod.run_turn_stream_async(
        "hi", agent=agent, thread_id="t-none", renderer=renderer, persist=False,
    )

    assert renderer.handled == []
    assert agent.astream_calls == 1
