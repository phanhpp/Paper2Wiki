"""Each path driven through the real middleware, with a fake model.

The other test modules call check functions directly with a hand-built
RunContext. That proves the checks work, but not that a path ever *fires* — the
snapshot, classification and check wiring is untested by them. The marp path
once could not trigger at all (its files live outside the wiki, so the wiki
snapshot never saw them) and no check-level test could have caught it.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from src.middleware.types import Evaluation
from src.middleware.wiki_rubric import WikiRubricMiddleware
from tests.middleware.conftest import DECK, GRAPH_WITH_NODE, INDEX_WITH_PAGE, call

pytestmark = pytest.mark.unit

CFG = {"configurable": {"thread_id": "t"}}


def middleware(seen: list[Evaluation], **kwargs) -> WikiRubricMiddleware:
    return WikiRubricMiddleware(on_evaluation=seen.append, **kwargs)


def complete_ingest(wiki) -> list:
    """Tool calls for an ingest that satisfies every check."""
    return [
        call("write_file", {"file_path": "concepts/self-attention.md"}, "c1"),
        call("write_file", {"file_path": "index.md", "content": INDEX_WITH_PAGE}, "c2"),
        call("write_file", {"file_path": "log.md",
                            "content": "# Wiki Log\n\n## [2026-01-01] ingest | self-attention\n"}, "c3"),
        call("write_file", {"file_path": "graph/graph.json", "content": GRAPH_WITH_NODE}, "c4"),
        AIMessage("Ingested."),
    ]


# ---------------------------------------------------------------- ingest ----

@pytest.mark.asyncio
async def test_ingest_clean_run_passes_with_no_retry(wiki, agent_for):
    seen: list[Evaluation] = []
    # transformer-architecture is referenced by the graph, so it must exist
    (wiki / "concepts" / "transformer-architecture.md").write_text("x", encoding="utf-8")

    agent = agent_for(complete_ingest(wiki), middleware(seen))
    await agent.ainvoke({"messages": [("user", "ingest the attention paper")]}, config=CFG)

    assert [e.result for e in seen] == ["satisfied"]


@pytest.mark.asyncio
async def test_ingest_failing_run_retries_then_gives_up(wiki, agent_for):
    seen: list[Evaluation] = []
    # writes the page and nothing else, every attempt
    responses = []
    for i in range(4):
        responses += [call("write_file", {"file_path": "concepts/self-attention.md"}, f"c{i}"),
                      AIMessage("Done.")]

    agent = agent_for(responses, middleware(seen, max_iterations=2))
    await agent.ainvoke({"messages": [("user", "ingest the attention paper")]}, config=CFG)

    assert [e.result for e in seen] == [
        "needs_revision", "needs_revision", "max_iterations_reached",
    ]
    assert {c.id for c in seen[-1].failed} >= {"S4", "S5", "S6"}


@pytest.mark.asyncio
async def test_ingest_that_fixes_itself_on_retry_ends_satisfied(wiki, agent_for):
    """The loop's whole purpose: a bad first attempt, corrected after feedback."""
    seen: list[Evaluation] = []
    (wiki / "concepts" / "transformer-architecture.md").write_text("x", encoding="utf-8")

    responses = [
        # attempt 1 — page only, will fail
        call("write_file", {"file_path": "concepts/self-attention.md"}, "c0"),
        AIMessage("Done."),
        # attempt 2 — completes the rest
        *complete_ingest(wiki),
    ]
    agent = agent_for(responses, middleware(seen, max_iterations=2))
    await agent.ainvoke({"messages": [("user", "ingest the attention paper")]}, config=CFG)

    assert [e.result for e in seen] == ["needs_revision", "satisfied"]


@pytest.mark.asyncio
async def test_reingest_that_writes_nothing_is_not_a_failure(wiki, agent_for):
    """Already ingested, so the agent correctly writes nothing. Must not be graded."""
    seen: list[Evaluation] = []
    agent = agent_for([AIMessage("Already in the wiki — nothing to do.")], middleware(seen))
    await agent.ainvoke({"messages": [("user", "ingest the attention paper")]}, config=CFG)

    assert seen == []


# ----------------------------------------------------------------- query ----

@pytest.mark.asyncio
async def test_query_with_proper_citation_passes(wiki, agent_for):
    seen: list[Evaluation] = []
    (wiki / "concepts" / "self-attention.md").write_text("about attention", encoding="utf-8")

    responses = [
        call("read_file", {"file_path": "/wiki/concepts/self-attention.md"}, "c1"),
        AIMessage("See [[self-attention]] — it weighs tokens against each other."),
    ]
    agent = agent_for(responses, middleware(seen))
    await agent.ainvoke({"messages": [("user", "use the wiki: what is self-attention?")]}, config=CFG)

    assert [e.result for e in seen] == ["satisfied"]


@pytest.mark.asyncio
async def test_query_answered_from_memory_fails(wiki, agent_for):
    """Asked for the wiki, cited nothing — the worst failure for a knowledge base."""
    seen: list[Evaluation] = []
    agent = agent_for([AIMessage("Attention weighs tokens by relevance.")],
                      middleware(seen, max_iterations=1))
    await agent.ainvoke({"messages": [("user", "use the wiki: what is self-attention?")]}, config=CFG)

    assert seen[0].result == "needs_revision"
    assert any(c.id == "Q1" for c in seen[0].failed)


@pytest.mark.asyncio
async def test_query_citing_an_unopened_page_fails(wiki, agent_for):
    """Read one page, cited a different one — a fabricated attribution."""
    seen: list[Evaluation] = []
    (wiki / "concepts" / "self-attention.md").write_text("x", encoding="utf-8")
    (wiki / "concepts" / "transformer-architecture.md").write_text("x", encoding="utf-8")

    responses = [
        call("read_file", {"file_path": "/wiki/concepts/self-attention.md"}, "c1"),
        AIMessage("See [[transformer-architecture]]."),
    ]
    agent = agent_for(responses, middleware(seen, max_iterations=1))
    await agent.ainvoke({"messages": [("user", "use the wiki: what is attention?")]}, config=CFG)

    assert seen[0].result == "needs_revision"
    assert any(c.id == "Q2" for c in seen[0].failed)


@pytest.mark.asyncio
async def test_query_page_found_by_grep_counts_as_seen(wiki, agent_for):
    """grep returns matching paths, so the agent really has seen the page.

    Regression: reads used to come only from read_file, which failed this run
    even though nothing was wrong with it.
    """
    seen: list[Evaluation] = []
    (wiki / "concepts" / "self-attention.md").write_text("all about attention", encoding="utf-8")

    responses = [
        call("grep", {"pattern": "attention", "path": "/wiki/"}, "c1"),
        AIMessage("See [[self-attention]]."),
    ]
    agent = agent_for(responses, middleware(seen))
    await agent.ainvoke({"messages": [("user", "use the wiki: what is attention?")]}, config=CFG)

    assert [e.result for e in seen] == ["satisfied"]


@pytest.mark.asyncio
async def test_question_not_asking_for_the_wiki_is_ungraded(wiki, agent_for):
    seen: list[Evaluation] = []
    agent = agent_for([AIMessage("Attention weighs tokens by relevance.")], middleware(seen))
    await agent.ainvoke({"messages": [("user", "what is an attention mechanism?")]}, config=CFG)

    assert seen == []


@pytest.mark.asyncio
async def test_query_persisted_without_index_entry_fails(wiki, agent_for):
    """Saving is optional, but a saved answer is a wiki page like any other."""
    seen: list[Evaluation] = []
    (wiki / "concepts" / "self-attention.md").write_text("x", encoding="utf-8")

    responses = [
        call("read_file", {"file_path": "/wiki/concepts/self-attention.md"}, "c1"),
        call("write_file", {"file_path": "queries/what-is-attention.md",
                            "content": "no frontmatter"}, "c2"),
        AIMessage("Saved. See [[self-attention]]."),
    ]
    agent = agent_for(responses, middleware(seen, max_iterations=1))
    await agent.ainvoke({"messages": [("user", "use the wiki: what is attention?")]}, config=CFG)

    assert seen[0].result == "needs_revision"
    assert any(c.id == "Q3" for c in seen[0].failed)


# ------------------------------------------------------------------ marp ----

@pytest.mark.asyncio
async def test_marp_deck_detected_from_the_artifacts_dir(wiki, agent_for, slides):
    """marp-slides/ is outside the wiki, so it needs its own snapshot.

    Regression: with only a wiki snapshot this path never fired and decks went
    permanently unchecked.
    """
    seen: list[Evaluation] = []
    agent = agent_for([call("save_slides", {"name": "deck.md"}, "c1"), AIMessage("Slides ready.")],
                      middleware(seen))
    await agent.ainvoke({"messages": [("user", "make me a deck about transformers")]}, config=CFG)

    assert [e.result for e in seen] == ["satisfied"]
    assert (slides / "deck.md").exists()


@pytest.mark.asyncio
async def test_marp_claiming_slides_without_downloading_fails(wiki, agent_for):
    """Built in the sandbox but never downloaded — the user is told slides exist
    while nothing reached the host."""
    seen: list[Evaluation] = []
    agent = agent_for([AIMessage("Your slides are ready!")], middleware(seen, max_iterations=1))
    await agent.ainvoke({"messages": [("user", "make me a deck")]}, config=CFG)

    # No artifacts and no marp tool used, so there is nothing to classify.
    assert seen == []


@pytest.mark.asyncio
async def test_marp_deck_without_the_marp_directive_fails(wiki, agent_for, slides):
    """Without `marp: true` it renders as plain markdown — looks fine, no slides."""
    seen: list[Evaluation] = []
    broken = DECK.replace("marp: true\n", "")

    responses = [call("save_slides", {"name": "deck.md", "content": broken}, "c1"),
                 AIMessage("Slides ready.")]
    agent = agent_for(responses, middleware(seen, max_iterations=1))
    await agent.ainvoke({"messages": [("user", "make me a deck")]}, config=CFG)

    assert seen[0].result == "needs_revision"
    assert any(c.id == "M2" for c in seen[0].failed)


@pytest.mark.asyncio
async def test_marp_single_slide_deck_fails(wiki, agent_for):
    """One slide means the `---` separators were omitted."""
    seen: list[Evaluation] = []
    one_slide = "---\nmarp: true\n---\n\n# Only slide\n"

    responses = [call("save_slides", {"name": "deck.md", "content": one_slide}, "c1"),
                 AIMessage("Slides ready.")]
    agent = agent_for(responses, middleware(seen, max_iterations=1))
    await agent.ainvoke({"messages": [("user", "make me a deck")]}, config=CFG)

    assert seen[0].result == "needs_revision"
    assert any(c.id == "M3" for c in seen[0].failed)


# -------------------------------------------------------------- combined ----

@pytest.mark.asyncio
async def test_one_run_can_match_two_paths(wiki, agent_for, slides):
    """"Ingest this and make slides" is genuinely both — run both check sets."""
    seen: list[Evaluation] = []
    (wiki / "concepts" / "transformer-architecture.md").write_text("x", encoding="utf-8")

    responses = [
        *complete_ingest(wiki)[:-1],                       # drop the trailing AIMessage
        call("save_slides", {"name": "deck.md"}, "c5"),
        AIMessage("Ingested and slides made."),
    ]
    agent = agent_for(responses, middleware(seen))
    await agent.ainvoke({"messages": [("user", "ingest the attention paper and make slides")]},
                        config=CFG)

    ids = {c.id for c in seen[0].criteria}
    assert any(i.startswith("S") for i in ids), "ingest checks did not run"
    assert any(i.startswith("M") for i in ids), "marp checks did not run"


# --------------------------------------------------------- cross-cutting ----

@pytest.mark.asyncio
async def test_a_crashing_check_is_reported_not_fatal(wiki, agent_for, monkeypatch):
    """A broken check is our bug. It must not take the agent down with it."""
    seen: list[Evaluation] = []

    def boom(ctx):
        raise RuntimeError("bad check")

    monkeypatch.setattr("src.middleware.checks.CHECKS", {"ingest": [boom]})

    agent = agent_for([call("write_file", {"file_path": "concepts/x.md"}, "c1"),
                       AIMessage("Done.")], middleware(seen))
    result = await agent.ainvoke({"messages": [("user", "ingest something")]}, config=CFG)

    assert result["messages"], "the run should still return an answer"
    assert seen[0].result == "check_error"
