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
