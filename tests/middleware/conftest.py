"""Shared fixtures for driving the middleware with a fake model.

The point of these is to exercise the *wiring* — snapshot, classify, check,
retry — not the check logic itself, which is covered directly in the other test
modules. Bugs only visible here are the ones where a path never fires at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from langgraph.checkpoint.memory import InMemorySaver

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

INDEX_WITH_PAGE = """# Wiki Index

## Concepts
[[self-attention]] — Attention over a single sequence
"""

GRAPH_WITH_NODE = """{
  "nodes": [
    {"id": "self-attention", "type": "concept", "path": "concepts/self-attention.md"},
    {"id": "transformer-architecture", "type": "concept", "path": "concepts/transformer-architecture.md"}
  ],
  "edges": [
    {"source": "transformer-architecture", "target": "self-attention", "relation": "uses"}
  ]
}"""

DECK = """---
marp: true
theme: default
---

# Title

---

## Second

- point
"""


class FakeWithTools(FakeMessagesListChatModel):
    """FakeMessagesListChatModel refuses bind_tools; canned responses drive the loop."""

    def bind_tools(self, tools, **kwargs):
        return self


@pytest.fixture
def wiki(tmp_path, monkeypatch):
    """An empty-ish wiki, with get_wiki_root and the artifacts dir redirected here."""
    root = tmp_path / "wiki"
    for sub in ("concepts", "entities", "queries", "graph"):
        (root / sub).mkdir(parents=True)
    (root / "raw" / "papers").mkdir(parents=True)
    (root / "index.md").write_text("# Wiki Index\n", encoding="utf-8")
    (root / "log.md").write_text("# Wiki Log\n", encoding="utf-8")
    (root / "graph" / "graph.json").write_text('{"nodes": [], "edges": []}', encoding="utf-8")
    (root / "raw" / "papers" / "attention.pdf").write_bytes(b"%PDF fake")

    slides = tmp_path / "marp-slides"
    slides.mkdir()

    monkeypatch.setattr("src.middleware.wiki_rubric.get_wiki_root", lambda: root)
    monkeypatch.setattr("src.middleware.wiki_rubric.ARTIFACTS_DIR", slides)
    return root


@pytest.fixture
def slides(wiki) -> Path:
    """The marp-slides dir the middleware is watching (sibling of the wiki)."""
    return wiki.parent / "marp-slides"


@pytest.fixture
def agent_for(wiki, slides):
    """Build an agent whose tools really touch the temp filesystem.

    Tools mirror the real ones closely enough to matter: write_file takes
    `file_path`, read_file takes `file_path`, and grep returns matching paths in
    its *result* rather than its arguments.
    """

    def build(responses, middleware):
        @tool
        def write_file(file_path: str, content: str = PAGE) -> str:
            """Write a file under the wiki."""
            target = wiki / file_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"wrote {file_path}"

        @tool
        def read_file(file_path: str) -> str:
            """Read a file."""
            try:
                return (wiki / file_path.lstrip("/").removeprefix("wiki/")).read_text()
            except OSError:
                return "not found"

        @tool
        def grep(pattern: str, path: str = "/") -> str:
            """Search the wiki; returns matching file paths, as the real tool does."""
            hits = [
                f"/wiki/{p.relative_to(wiki)}"
                for p in wiki.rglob("*.md")
                if pattern.lower() in p.read_text().lower() or pattern.lower() in p.stem
            ]
            return "\n".join(hits) or "No files found"

        @tool
        def save_slides(name: str, content: str = DECK) -> str:
            """Download a built deck out of the sandbox into marp-slides/."""
            (slides / name).write_text(content, encoding="utf-8")
            return f"saved {name}"

        return create_agent(
            model=FakeWithTools(responses=responses),
            tools=[write_file, read_file, grep, save_slides],
            middleware=[middleware],
            checkpointer=InMemorySaver(),
        )

    return build


def call(name: str, args: dict, call_id: str) -> AIMessage:
    """An AI message that invokes one tool."""
    return AIMessage("", tool_calls=[{"name": name, "args": args, "id": call_id}])
