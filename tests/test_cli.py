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


def _interrupt(action_name: str = "write_file", args: dict | None = None):
    """Build a single fake LangGraph interrupt matching the HITL payload shape."""
    return SimpleNamespace(
        value={
            "action_requests": [{"name": action_name, "args": args or {"path": "a.md"}}],
            "review_configs": [
                {"action_name": action_name, "allowed_decisions": ["approve", "edit", "reject"]}
            ],
        }
    )


def _feed_input(monkeypatch, responses):
    """Patch builtins.input to return queued responses in order."""
    queue = list(responses)
    monkeypatch.setattr("builtins.input", lambda *a, **k: queue.pop(0))


@pytest.mark.unit
def test_handle_interrupts_approve(monkeypatch):
    _feed_input(monkeypatch, ["a"])
    decisions = DefaultRenderer().handle_interrupts([_interrupt()])
    assert decisions == [{"type": "approve"}]


@pytest.mark.unit
def test_handle_interrupts_reject(monkeypatch):
    _feed_input(monkeypatch, ["r"])
    decisions = DefaultRenderer().handle_interrupts([_interrupt()])
    assert decisions == [{"type": "reject"}]


@pytest.mark.unit
def test_handle_interrupts_edit_json(monkeypatch):
    _feed_input(monkeypatch, ["e", '{"path": "b.md"}'])
    decisions = DefaultRenderer().handle_interrupts([_interrupt()])
    assert decisions == [
        {"type": "edit", "edited_action": {"name": "write_file", "args": {"path": "b.md"}}}
    ]


@pytest.mark.unit
def test_handle_interrupts_edit_python_dict(monkeypatch):
    # Not valid JSON (single quotes) but a valid Python literal → ast.literal_eval path.
    _feed_input(monkeypatch, ["e", "{'path': 'c.md'}"])
    decisions = DefaultRenderer().handle_interrupts([_interrupt()])
    assert decisions == [
        {"type": "edit", "edited_action": {"name": "write_file", "args": {"path": "c.md"}}}
    ]


@pytest.mark.unit
def test_handle_interrupts_edit_invalid_falls_back_to_approve(monkeypatch):
    _feed_input(monkeypatch, ["e", "not parseable !!"])
    decisions = DefaultRenderer().handle_interrupts([_interrupt()])
    assert decisions == [{"type": "approve"}]


@pytest.mark.unit
def test_handle_interrupts_yolo_sets_session_auto_approve(monkeypatch):
    renderer = DefaultRenderer()
    _feed_input(monkeypatch, ["yolo"])
    first = renderer.handle_interrupts([_interrupt()])
    assert first == [{"type": "approve"}]
    assert renderer.auto_approve is True

    # Subsequent interrupts auto-approve without prompting (input would raise IndexError).
    second = renderer.handle_interrupts([_interrupt("edit_file")])
    assert second == [{"type": "approve"}]


@pytest.mark.unit
def test_auto_approve_constructor_short_circuits(monkeypatch):
    # auto_approve=True must never call input().
    def _boom(*a, **k):
        raise AssertionError("input() should not be called when auto_approve=True")

    monkeypatch.setattr("builtins.input", _boom)
    decisions = DefaultRenderer(auto_approve=True).handle_interrupts([_interrupt()])
    assert decisions == [{"type": "approve"}]


@pytest.mark.unit
def test_rich_renderer_maps_choices_via_rich_prompt(monkeypatch):
    # RichRenderer reads the HITL choice through rich.prompt.Prompt.ask, not input().
    import src.cli.renderer as rr

    monkeypatch.setattr(rr.Prompt, "ask", staticmethod(lambda *a, **k: "r"))
    renderer = rr.RichRenderer()
    decisions = renderer.handle_interrupts([_interrupt()])
    assert decisions == [{"type": "reject"}]


@pytest.mark.unit
def test_rich_renderer_edit_uses_rich_prompt(monkeypatch):
    import src.cli.renderer as rr

    answers = iter(["e", '{"path": "z.md"}'])
    monkeypatch.setattr(rr.Prompt, "ask", staticmethod(lambda *a, **k: next(answers)))
    renderer = rr.RichRenderer()
    decisions = renderer.handle_interrupts([_interrupt()])
    assert decisions == [
        {"type": "edit", "edited_action": {"name": "write_file", "args": {"path": "z.md"}}}
    ]


@pytest.mark.unit
def test_apply_env_sets_ingest_mode_read_by_get_ingest_mode(monkeypatch):
    from src.cli._env import IngestMode, apply_env
    from src.ingest_mode import get_ingest_mode

    monkeypatch.delenv("PAPER2WIKI_INGEST_MODE", raising=False)
    apply_env(IngestMode.quality, None)
    assert get_ingest_mode() == "quality"


@pytest.mark.unit
def test_apply_env_sets_wiki_path(monkeypatch):
    import os

    from src.cli._env import apply_env

    monkeypatch.delenv("WIKI_PATH", raising=False)
    apply_env(None, "/tmp/custom-wiki")
    assert os.environ["WIKI_PATH"] == "/tmp/custom-wiki"


@pytest.mark.unit
def test_apply_env_none_is_noop(monkeypatch):
    from src.cli._env import apply_env

    monkeypatch.setenv("PAPER2WIKI_INGEST_MODE", "fast")
    apply_env(None, None)
    import os

    assert os.environ["PAPER2WIKI_INGEST_MODE"] == "fast"


@pytest.mark.unit
def test_require_keys_raises_when_anthropic_missing(monkeypatch):
    import typer

    from src.cli._env import require_keys

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("DAYTONA_API_KEY", "x")
    with pytest.raises(typer.Exit):
        require_keys(eval_mode=False)


@pytest.mark.unit
def test_require_keys_eval_mode_skips_daytona(monkeypatch):
    from src.cli._env import require_keys

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    monkeypatch.delenv("DAYTONA_API_KEY", raising=False)
    # Should not raise: Daytona not required in eval mode.
    require_keys(eval_mode=True)
