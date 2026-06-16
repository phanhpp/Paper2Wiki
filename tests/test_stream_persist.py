"""Unit tests for the ``persist`` switch on ``run_turn_stream_async``.

``persist=False`` (the CLI's ``--no-save``) must skip sessions.db entirely: no
``aget_state`` snapshot, no ``_save_session`` row/title. ``persist=True`` keeps the
existing behaviour. All I/O is mocked — no DB, no network, no LLM.
"""

import pytest

import src.agents.stream as stream_mod


class _FakeState:
    values = {"messages": []}


class _FakeAgent:
    """Minimal agent: yields no stream chunks and reports empty final state."""

    async def astream(self, *args, **kwargs):
        for chunk in ():  # empty async generator → loop exits with no interrupts
            yield chunk

    async def aget_state(self, config):
        return _FakeState()


@pytest.mark.unit
async def test_persist_false_skips_sessions_db(monkeypatch):
    save_calls, state_calls = [], []
    monkeypatch.setattr(stream_mod, "_save_session", lambda *a, **k: save_calls.append(1))
    monkeypatch.setattr(
        stream_mod, "get_sessions_conn",
        lambda: (_ for _ in ()).throw(AssertionError("sessions DB opened with persist=False")),
    )

    agent = _FakeAgent()
    monkeypatch.setattr(agent, "aget_state",
                        lambda config: state_calls.append(1) or _FakeState())

    await stream_mod.run_turn_stream_async(
        "hi", agent=agent, thread_id="t-nosave", persist=False,
    )

    assert save_calls == []   # nothing written
    assert state_calls == []  # snapshot not even taken


@pytest.mark.unit
async def test_persist_true_saves(monkeypatch):
    save_calls = []
    monkeypatch.setattr(stream_mod, "_save_session", lambda *a, **k: save_calls.append(1))
    monkeypatch.setattr(stream_mod, "get_sessions_conn", lambda: object())

    await stream_mod.run_turn_stream_async(
        "hi", agent=_FakeAgent(), thread_id="t-save", persist=True,
    )

    assert save_calls == [1]


class _SpyRenderer:
    """Records which renderer hook each streamed message hits."""

    auto_approve = False

    def __init__(self):
        self.tokens: list[str] = []
        self.tool_results: list[tuple[str, str]] = []

    def on_turn_start(self): ...
    def on_token(self, text): self.tokens.append(text)
    def on_tool_call(self, name, args): ...
    def on_tool_result(self, name, content): self.tool_results.append((name, content))
    def on_turn_end(self): ...
    def on_debug(self, message): ...
    def handle_interrupts(self, interrupts): return []


class _MessagesAgent:
    """Streams one AI text chunk then one ToolMessage on the 'messages' channel."""

    def __init__(self, ai_msg, tool_msg):
        self._chunks = [
            {"type": "messages", "data": (ai_msg, {})},
            {"type": "messages", "data": (tool_msg, {})},
        ]

    async def astream(self, *args, **kwargs):
        for chunk in self._chunks:
            yield chunk


@pytest.mark.unit
async def test_tool_message_routes_to_on_tool_result_not_on_token():
    """ToolMessage content goes to on_tool_result (collapsed preview), not the
    live Markdown stream — assistant text still streams via on_token."""
    from langchain_core.messages import AIMessageChunk, ToolMessage

    ai = AIMessageChunk(content="hello")
    tool = ToolMessage(content="FILE BODY", tool_call_id="x", name="read_file")
    renderer = _SpyRenderer()

    await stream_mod.run_turn_stream_async(
        "hi", agent=_MessagesAgent(ai, tool), thread_id="t-route",
        renderer=renderer, persist=False,
    )

    assert renderer.tokens == ["hello"]                       # AI text streamed
    assert renderer.tool_results == [("read_file", "FILE BODY")]  # tool routed away from on_token
