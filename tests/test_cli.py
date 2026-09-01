"""Unit tests for the CLI: renderer HITL decision mapping and env resolution.

All tests are pure — no network, no agent, no real files. They cover:
- ``build_decisions`` / ``DefaultRenderer.handle_interrupts``: approve / reject / edit
  (JSON + Python-dict + invalid) / yolo, and the session auto-approve short-circuit.
- ``apply_env``: ``--ingest-mode`` / ``--wiki-path`` flags flow into the env vars that
  ``get_ingest_mode`` already reads.
- ``require_keys``: fails fast on missing credentials, and exempts Daytona in eval mode.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.agents.renderer import DefaultRenderer


def _interrupt(action_name: str = "write_file", args: dict | None = None,
               allowed: list[str] | None = None):
    """Build a single fake LangGraph interrupt matching the HITL payload shape."""
    return SimpleNamespace(
        value={
            "action_requests": [{"name": action_name, "args": args or {"path": "a.md"}}],
            "review_configs": [
                {"action_name": action_name,
                 "allowed_decisions": allowed or ["approve", "edit", "reject"]}
            ],
        }
    )


def _feed_input(monkeypatch, responses):
    """Patch builtins.input to return queued responses in order."""
    queue = list(responses)
    monkeypatch.setattr("builtins.input", lambda *a, **k: queue.pop(0))


@pytest.mark.unit
async def test_handle_interrupts_approve(monkeypatch):
    """'a' (or any unrecognized choice) maps to an approve decision."""
    _feed_input(monkeypatch, ["a"])
    decisions = await DefaultRenderer().handle_interrupts([_interrupt()])
    assert decisions == [{"type": "approve"}]


@pytest.mark.unit
async def test_handle_interrupts_reject_no_reason(monkeypatch):
    """'r' with an empty reason maps to a bare reject decision."""
    _feed_input(monkeypatch, ["r", ""])
    decisions = await DefaultRenderer().handle_interrupts([_interrupt()])
    assert decisions == [{"type": "reject"}]


@pytest.mark.unit
async def test_handle_interrupts_reject_with_reason(monkeypatch):
    """'r' with a reason attaches it as the message sent back to the model."""
    _feed_input(monkeypatch, ["r", "use the feat/cli branch, not main"])
    decisions = await DefaultRenderer().handle_interrupts([_interrupt()])
    assert decisions == [{"type": "reject", "message": "use the feat/cli branch, not main"}]


@pytest.mark.unit
async def test_handle_interrupts_respond(monkeypatch):
    """'s' returns the typed text as a respond decision (ask-user tools)."""
    _feed_input(monkeypatch, ["s", "the answer is 42"])
    decisions = await DefaultRenderer().handle_interrupts(
        [_interrupt("ask_user", allowed=["approve", "respond"])]
    )
    assert decisions == [{"type": "respond", "message": "the answer is 42"}]


@pytest.mark.unit
def test_choices_for_filters_to_allowed():
    """Only tool-allowed decisions are offered; yolo rides along with approve."""
    from src.agents.renderer import choices_for

    assert choices_for(["approve", "reject"]) == ["a", "r", "yolo"]
    assert choices_for(["reject"]) == ["r"]  # no approve → no yolo
    assert choices_for(["approve", "edit", "reject", "respond"]) == ["a", "e", "r", "s", "yolo"]


@pytest.mark.unit
async def test_handle_interrupts_edit_json(monkeypatch):
    """'e' with JSON args produces an edit decision carrying the parsed args."""
    _feed_input(monkeypatch, ["e", '{"path": "b.md"}'])
    decisions = await DefaultRenderer().handle_interrupts([_interrupt()])
    assert decisions == [
        {"type": "edit", "edited_action": {"name": "write_file", "args": {"path": "b.md"}}}
    ]


@pytest.mark.unit
async def test_handle_interrupts_edit_python_dict(monkeypatch):
    """'e' with a Python-dict literal (not JSON) is parsed via the ast.literal_eval fallback."""
    _feed_input(monkeypatch, ["e", "{'path': 'c.md'}"])
    decisions = await DefaultRenderer().handle_interrupts([_interrupt()])
    assert decisions == [
        {"type": "edit", "edited_action": {"name": "write_file", "args": {"path": "c.md"}}}
    ]


@pytest.mark.unit
async def test_handle_interrupts_edit_invalid_falls_back_to_approve(monkeypatch):
    """'e' with unparseable args safely falls back to approve rather than crashing."""
    _feed_input(monkeypatch, ["e", "not parseable !!"])
    decisions = await DefaultRenderer().handle_interrupts([_interrupt()])
    assert decisions == [{"type": "approve"}]


@pytest.mark.unit
async def test_handle_interrupts_yolo_sets_session_auto_approve(monkeypatch):
    """'yolo' approves and latches auto-approve so later interrupts skip the prompt."""
    renderer = DefaultRenderer()
    _feed_input(monkeypatch, ["yolo"])
    first = await renderer.handle_interrupts([_interrupt()])
    assert first == [{"type": "approve"}]
    assert renderer.auto_approve is True

    # Subsequent interrupts auto-approve without prompting (input would raise IndexError).
    second = await renderer.handle_interrupts([_interrupt("edit_file")])
    assert second == [{"type": "approve"}]


@pytest.mark.unit
async def test_auto_approve_constructor_short_circuits(monkeypatch):
    """auto_approve=True approves without ever calling input()."""
    def _boom(*a, **k):
        raise AssertionError("input() should not be called when auto_approve=True")

    monkeypatch.setattr("builtins.input", _boom)
    decisions = await DefaultRenderer(auto_approve=True).handle_interrupts([_interrupt()])
    assert decisions == [{"type": "approve"}]


@pytest.mark.unit
async def test_rich_renderer_maps_choices_via_rich_prompt(monkeypatch):
    """RichRenderer reads the HITL choice via rich.prompt.Prompt.ask (not input())."""
    import src.cli.renderer as rr

    answers = iter(["r", ""])  # choice, then empty reason
    monkeypatch.setattr(rr.Prompt, "ask", staticmethod(lambda *a, **k: next(answers)))
    renderer = rr.RichRenderer()
    decisions = await renderer.handle_interrupts([_interrupt()])
    assert decisions == [{"type": "reject"}]


@pytest.mark.unit
async def test_rich_renderer_edit_uses_rich_prompt(monkeypatch):
    """RichRenderer's edit path reads both the choice and the new args via Prompt.ask."""
    import src.cli.renderer as rr

    answers = iter(["e", '{"path": "z.md"}'])
    monkeypatch.setattr(rr.Prompt, "ask", staticmethod(lambda *a, **k: next(answers)))
    renderer = rr.RichRenderer()
    decisions = await renderer.handle_interrupts([_interrupt()])
    assert decisions == [
        {"type": "edit", "edited_action": {"name": "write_file", "args": {"path": "z.md"}}}
    ]


@pytest.mark.unit
def test_renderers_conform_to_protocol():
    """All three renderers satisfy the Renderer protocol (incl. on_tool_result)."""
    from src.agents.renderer import Renderer
    import src.cli.renderer as rr
    from src.slack.renderer import SlackRenderer

    assert isinstance(DefaultRenderer(), Renderer)
    assert isinstance(rr.RichRenderer(), Renderer)
    assert isinstance(SlackRenderer(object(), "C1", "1.1"), Renderer)


@pytest.mark.unit
def test_rich_renderer_tool_result_previews_and_stashes_full():
    """on_tool_result keeps the full text but only previews it inline."""
    import src.cli.renderer as rr

    renderer = rr.RichRenderer()
    long = "\n".join(f"line {i}" for i in range(1, 51))
    renderer.on_turn_start()
    renderer.on_tool_result("read_file", long)

    assert renderer._last_tool_output == [("read_file", long)]  # full kept


@pytest.mark.unit
def test_rich_renderer_open_last_tool_output_pages_full(monkeypatch):
    """Ctrl-O handler pages the full stashed output with a per-tool header."""
    import src.cli.renderer as rr

    captured = {}
    monkeypatch.setattr(rr.click, "echo_via_pager", lambda text: captured.update(text=text))
    renderer = rr.RichRenderer()
    renderer.on_turn_start()
    renderer.on_tool_result("read_file", "line 1\nline 50")
    renderer.open_last_tool_output()

    assert "── read_file ──" in captured["text"]
    assert "line 50" in captured["text"]


@pytest.mark.unit
def test_rich_renderer_open_last_tool_output_empty(monkeypatch):
    """With nothing stashed, Ctrl-O reports it and never invokes the pager."""
    import src.cli.renderer as rr

    called = {"pager": False}
    monkeypatch.setattr(rr.click, "echo_via_pager", lambda text: called.update(pager=True))
    renderer = rr.RichRenderer()
    renderer.on_turn_start()  # clears the store
    renderer.open_last_tool_output()

    assert called["pager"] is False


@pytest.mark.unit
def test_rich_renderer_turn_start_clears_tool_output():
    """A new turn drops the previous turn's stashed output."""
    import src.cli.renderer as rr

    renderer = rr.RichRenderer()
    renderer.on_turn_start()
    renderer.on_tool_result("read_file", "data")
    assert renderer._last_tool_output
    renderer.on_turn_start()
    assert renderer._last_tool_output == []


@pytest.mark.unit
def test_apply_env_sets_ingest_mode_read_by_get_ingest_mode(monkeypatch):
    """--ingest-mode flows through apply_env into the env var get_ingest_mode reads."""
    from src.cli._env import IngestMode, apply_env
    from src.ingest_mode import get_ingest_mode

    monkeypatch.delenv("PAPER2WIKI_INGEST_MODE", raising=False)
    apply_env(IngestMode.quality, None)
    assert get_ingest_mode() == "quality"


@pytest.mark.unit
def test_apply_env_sets_wiki_path(monkeypatch):
    """--wiki-path is written to the WIKI_PATH env var."""
    import os

    from src.cli._env import apply_env

    monkeypatch.delenv("WIKI_PATH", raising=False)
    apply_env(None, "/tmp/custom-wiki")
    assert os.environ["WIKI_PATH"] == "/tmp/custom-wiki"


@pytest.mark.unit
def test_apply_env_none_is_noop(monkeypatch):
    """Passing None for both flags leaves existing env vars untouched."""
    from src.cli._env import apply_env

    monkeypatch.setenv("PAPER2WIKI_INGEST_MODE", "fast")
    apply_env(None, None)
    import os

    assert os.environ["PAPER2WIKI_INGEST_MODE"] == "fast"


@pytest.mark.unit
def test_require_keys_raises_when_anthropic_missing(monkeypatch):
    """A missing ANTHROPIC_API_KEY fails fast with typer.Exit."""
    import typer

    from src.cli._env import require_keys

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("DAYTONA_API_KEY", "x")
    with pytest.raises(typer.Exit):
        require_keys(eval_mode=False)


@pytest.mark.unit
def test_require_keys_eval_mode_skips_daytona(monkeypatch):
    """eval_mode skips the Daytona key requirement (no sandbox is built)."""
    from src.cli._env import require_keys

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    require_keys(eval_mode=True)  # must not raise
