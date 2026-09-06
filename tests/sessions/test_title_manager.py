"""Session auto-titling: it must survive a short process, and any provider's content shape.

Two bugs this pins, both of which were silent:

* **The titling thread is a daemon.** A one-shot ``chat`` exited immediately and killed it
  mid-call, so every one-shot session stayed ``untitled`` while the REPL — which stays
  alive for the next prompt — worked fine.
* **Content shape differs per provider.** Anthropic returns ``"hi"``; Gemini returns
  ``[{"type": "text", "text": "hi"}]``. Calling ``.strip()`` on the latter raises.
"""

from __future__ import annotations

import pytest

from src.text import as_text


# --- content normalisation -------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("content,expected", [
    ("plain string", "plain string"),                                  # Anthropic
    ([{"type": "text", "text": "block form"}], "block form"),          # Gemini
    ([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}], "a\nb"),
    ([], ""),
    (None, ""),
])
def test_as_text_flattens_every_provider_shape(content, expected):
    assert as_text(content) == expected


@pytest.mark.unit
def test_as_text_never_raises_on_an_unexpected_shape():
    """Last-resort stringification — a new provider shape must not crash titling."""
    assert as_text(42) == "42"
    assert as_text({"unexpected": "mapping"})


@pytest.mark.unit
def test_generate_title_accepts_gemini_style_block_content(monkeypatch):
    """The exact failure: 'list' object has no attribute 'strip'."""
    import src.sessions.title_manager as tm

    class _Resp:
        content = [{"type": "text", "text": "Understanding Knowledge Graphs"}]

    class _LLM:
        def invoke(self, _messages):
            return _Resp()

    monkeypatch.setattr(tm, "set_up_llms", lambda spec: _LLM())

    title = tm._generate_title(
        [{"type": "text", "text": "what is a knowledge graph?"}],   # inputs are blocks too
        [{"type": "text", "text": "A graph of entities and relations."}],
    )
    assert title == "Understanding Knowledge Graphs"


# --- the thread must be joinable --------------------------------------------------

@pytest.mark.unit
def test_maybe_auto_title_returns_its_thread(monkeypatch, tmp_path):
    """A short-lived caller has to be able to wait — the thread is a daemon.

    Returning None here would restore the bug: `chat` exits, the daemon dies, and the
    session is never titled.
    """
    import sqlite3

    import src.sessions.title_manager as tm

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO sessions (id, title) VALUES ('s1', NULL)")

    monkeypatch.setattr(tm, "_generate_title", lambda u, a: "Generated Title")
    monkeypatch.setattr(tm, "set_session_title", lambda c, sid, t: None)

    class _Msg:
        def __init__(self, type_, content):
            self.type, self.content = type_, content

    thread = tm.maybe_auto_title(conn, "s1", [_Msg("human", "q"), _Msg("ai", "a")])

    assert thread is not None, "callers must be able to join before exiting"
    thread.join(timeout=5)
    assert not thread.is_alive()


@pytest.mark.unit
def test_maybe_auto_title_returns_none_when_there_is_nothing_to_title():
    import sqlite3

    import src.sessions.title_manager as tm

    conn = sqlite3.connect(":memory:")

    class _Msg:
        def __init__(self, type_, content):
            self.type, self.content = type_, content

    # a user message with no assistant reply yet
    assert tm.maybe_auto_title(conn, "s1", [_Msg("human", "q")]) is None
