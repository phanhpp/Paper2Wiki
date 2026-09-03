"""Unit tests for WikiRubricMiddleware.

Fake model + InMemorySaver, no network. Covers the mechanics the design depends
on: per-turn state reset, filesystem-diff write detection (including shell
writes), the retry loop and its cap, and which verdicts reach on_evaluation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver

from src.middleware.types import Evaluation
from src.middleware.wiki_rubric import (
    WikiRubricMiddleware,
    append_or_reset,
    diff_dir,
    last_user_question,
    snapshot_dir,
)

pytestmark = pytest.mark.unit


class FakeWithTools(FakeMessagesListChatModel):
    """FakeMessagesListChatModel refuses bind_tools; canned responses drive the loop."""

    def bind_tools(self, tools, **kwargs):
        return self


PAGE = """---
title: Self-Attention
created: 2026-01-01
updated: 2026-01-01
type: concept
tags: [architecture]
sources: [raw/papers/attention.pdf]
---

Body.
"""


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    """Temp wiki, with get_wiki_root patched at every call site."""
    (tmp_path / "concepts").mkdir()
    (tmp_path / "graph").mkdir()
    (tmp_path / "raw" / "papers").mkdir(parents=True)
    (tmp_path / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    (tmp_path / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    (tmp_path / "graph" / "graph.json").write_text('{"nodes": [], "edges": []}', encoding="utf-8")
    (tmp_path / "raw" / "papers" / "attention.pdf").write_bytes(b"%PDF fake")

    monkeypatch.setattr("src.middleware.wiki_rubric.get_wiki_root", lambda: tmp_path)
    return tmp_path


def make_agent(wiki: Path, responses, middleware):
    """Agent whose write_file tool really writes into the temp wiki."""

    @tool
    def write_file(file_path: str, content: str = PAGE) -> str:
        """Write a wiki page."""
        target = wiki / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"wrote {file_path}"

    return create_agent(
        model=FakeWithTools(responses=responses),
        tools=[write_file],
        middleware=[middleware],
        checkpointer=InMemorySaver(),
    )


def write_call(path: str, call_id: str):
    return AIMessage("", tool_calls=[{"name": "write_file", "args": {"file_path": path}, "id": call_id}])


# --- helpers ----------------------------------------------------------------

def test_append_or_reset_appends_and_clears():
    assert append_or_reset([], ["a"]) == ["a"]
    assert append_or_reset(["a"], ["b"]) == ["a", "b"]
    assert append_or_reset(["a", "b"], None) == []


def test_snapshot_diff_detects_create_and_append(wiki):
    before = snapshot_dir(wiki)

    (wiki / "concepts" / "new.md").write_text("x", encoding="utf-8")
    with (wiki / "log.md").open("a", encoding="utf-8") as fh:
        fh.write("\n## [2026-01-01] ingest | new\n")

    changed = diff_dir(before, wiki)
    assert "concepts/new.md" in changed
    assert "log.md" in changed          # in-place append, caught via size
    assert "index.md" not in changed    # untouched


def test_last_user_question_skips_injected_feedback():
    from langchain_core.messages import HumanMessage

    messages = [
        HumanMessage("turn 1 question"),
        AIMessage("answer"),
        HumanMessage("turn 2 question"),
        AIMessage("answer"),
        HumanMessage("FEEDBACK: fix S4", name="wiki_rubric",
                     additional_kwargs={"lc_source": "wiki_rubric"}),
    ]
    # Not messages[0] ("turn 1"), and not our own feedback.
    assert last_user_question(messages) == "turn 2 question"


# --- middleware behaviour ---------------------------------------------------

@pytest.mark.asyncio
async def test_plain_chat_is_ungraded(wiki):
    seen: list[Evaluation] = []
    agent = make_agent(wiki, [AIMessage("you're welcome")],
                       WikiRubricMiddleware(on_evaluation=seen.append))

    await agent.ainvoke({"messages": [("user", "thanks!")]},
                        config={"configurable": {"thread_id": "t"}})

    assert seen == []  # no_path_matched never reaches the callback


@pytest.mark.asyncio
async def test_failing_ingest_retries_then_stops_at_cap(wiki):
    seen: list[Evaluation] = []
    # Every attempt writes the page but never updates index/log/graph, so the
    # ingest checks keep failing.
    responses = [
        write_call("concepts/self-attention.md", "c1"), AIMessage("done"),
        write_call("concepts/self-attention.md", "c2"), AIMessage("done"),
        write_call("concepts/self-attention.md", "c3"), AIMessage("done"),
        AIMessage("done"),
    ]
    agent = make_agent(wiki, responses,
                       WikiRubricMiddleware(max_iterations=2, on_evaluation=seen.append))

    await agent.ainvoke({"messages": [("user", "ingest the attention paper")]},
                        config={"configurable": {"thread_id": "t"}})

    verdicts = [e.result for e in seen]
    assert verdicts == ["needs_revision", "needs_revision", "max_iterations_reached"]
    assert [e.iteration for e in seen] == [0, 1, 2]

    final = seen[-1]
    assert any(c.id == "S4" for c in final.failed)   # index.md entry missing
    assert any(c.id == "S5" for c in final.failed)   # log.md not appended


@pytest.mark.asyncio
async def test_shell_write_is_detected_without_a_tool_call(wiki):
    """The filesystem diff sees writes awrap_tool_call cannot."""
    seen: list[Evaluation] = []

    class ShellWriting(WikiRubricMiddleware):
        def after_agent(self, state, runtime):
            # Simulate a page written via shell `execute` — no tool call recorded.
            (wiki / "concepts" / "sneaky.md").write_text(PAGE, encoding="utf-8")
            return super().after_agent(state, runtime)

    agent = make_agent(wiki, [AIMessage("done")],
                       ShellWriting(on_evaluation=seen.append))

    await agent.ainvoke({"messages": [("user", "do something")]},
                        config={"configurable": {"thread_id": "t"}})

    assert seen, "a write outside the tool layer should still be classified"
    assert seen[-1].result in {"needs_revision", "max_iterations_reached"}


@pytest.mark.asyncio
async def test_state_resets_between_turns(wiki):
    """Turn 2 must not re-grade turn 1's writes."""
    seen: list[Evaluation] = []
    responses = [
        write_call("concepts/self-attention.md", "c1"), AIMessage("ingested"),
        write_call("concepts/self-attention.md", "c2"), AIMessage("ingested"),
        write_call("concepts/self-attention.md", "c3"), AIMessage("ingested"),
        AIMessage("you're welcome"),
    ]
    agent = make_agent(wiki, responses,
                       WikiRubricMiddleware(max_iterations=2, on_evaluation=seen.append))
    cfg = {"configurable": {"thread_id": "t"}}

    await agent.ainvoke({"messages": [("user", "ingest the attention paper")]}, config=cfg)
    turn_one = len(seen)

    await agent.ainvoke({"messages": [("user", "thanks!")]}, config=cfg)

    assert len(seen) == turn_one, "turn 2 was graded — turn 1's writes leaked"


@pytest.mark.asyncio
async def test_disabled_middleware_is_inert(wiki):
    seen: list[Evaluation] = []
    responses = [write_call("concepts/self-attention.md", "c1"), AIMessage("done")]
    agent = make_agent(wiki, responses,
                       WikiRubricMiddleware(enabled=False, on_evaluation=seen.append))

    await agent.ainvoke({"messages": [("user", "ingest the attention paper")]},
                        config={"configurable": {"thread_id": "t"}})

    assert seen == []


@pytest.mark.asyncio
async def test_broken_callback_does_not_crash_the_run(wiki):
    def explode(_evaluation):
        raise RuntimeError("consumer bug")

    responses = [write_call("concepts/self-attention.md", "c1"), AIMessage("done")]
    agent = make_agent(wiki, responses, WikiRubricMiddleware(on_evaluation=explode))

    result = await agent.ainvoke({"messages": [("user", "ingest the attention paper")]},
                                 config={"configurable": {"thread_id": "t"}})
    assert result["messages"]


def test_constructor_rejects_bad_max_iterations():
    with pytest.raises(ValueError):
        WikiRubricMiddleware(max_iterations=0)
    with pytest.raises(TypeError):
        WikiRubricMiddleware(max_iterations=True)


def test_record_captures_reads_from_args_and_from_results():
    """Two ways a tool names a file, and both count as the agent having seen it.

    read_file names it in the arguments. grep/glob name it in the *result* —
    their `path` argument is the directory searched, so recording that would log
    "/wiki/" as a page and make Q2 fail a search-then-cite run.
    """
    from langchain_core.messages import ToolMessage

    mw = WikiRubricMiddleware()

    class Req:
        def __init__(self, name, args):
            self.tool_call = {"name": name, "args": args}

    # read_file — path comes from the arguments
    assert mw._record(Req("read_file", {"file_path": "/wiki/concepts/x.md"})) == {
        "run_tools": ["read_file"],
        "run_reads": ["/wiki/concepts/x.md"],
    }

    # grep — the directory argument is ignored, the matched files are recorded
    grep_out = ToolMessage(
        content="/wiki/concepts/self-attention.md\n/wiki/concepts/transformer-architecture.md",
        tool_call_id="c1",
    )
    assert mw._record(Req("grep", {"pattern": "attention", "path": "/wiki/"}), grep_out) == {
        "run_tools": ["grep"],
        "run_reads": [
            "/wiki/concepts/self-attention.md",
            "/wiki/concepts/transformer-architecture.md",
        ],
    }

    # grep with output_mode="content" — paths still recoverable from the lines
    content_out = ToolMessage(
        content="/wiki/concepts/self-attention.md:12: attention weighs tokens",
        tool_call_id="c2",
    )
    assert mw._record(Req("grep", {"pattern": "attention"}), content_out)["run_reads"] == [
        "/wiki/concepts/self-attention.md"
    ]

    # a search that matched nothing records the tool but no reads
    empty = ToolMessage(content="No files found", tool_call_id="c3")
    assert mw._record(Req("glob", {"pattern": "**/*.md"}), empty) == {"run_tools": ["glob"]}


@pytest.mark.unit
def test_tool_returning_a_command_is_merged_not_nested():
    """A tool that already returns a Command (the subagent `task` tool does).

    Nesting it under "messages" hands a Command to the messages reducer, which
    raises `Unsupported message type: <class 'langgraph.types.Command'>` and
    kills the whole turn. Hit live by a marp request.
    """
    from langgraph.types import Command

    mw = WikiRubricMiddleware(enabled=True)

    class _Req:
        tool_call = {"name": "task", "args": {"description": "make slides"}}

    inner = Command(update={"messages": ["the subagent's reply"], "other": 1})
    out = mw._with_record(_Req(), inner)

    assert isinstance(out, Command)
    assert out.update["messages"] == ["the subagent's reply"]  # untouched
    assert out.update["other"] == 1                            # untouched
    assert out.update["run_tools"] == ["task"]                 # ours merged in


@pytest.mark.unit
def test_normal_tool_result_is_still_wrapped_in_messages():
    """The ordinary path must keep passing the result back to the model."""
    mw = WikiRubricMiddleware(enabled=True)

    class _Req:
        tool_call = {"name": "grep", "args": {"pattern": "x"}}

    out = mw._with_record(_Req(), "plain result")
    assert out.update["messages"] == ["plain result"]
    assert out.update["run_tools"] == ["grep"]
