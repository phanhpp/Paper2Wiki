"""Unit tests for checks/common.py — the wiki file parsing every check depends on.

These helpers were previously exercised only indirectly, through the checks that
call them. That means a bug in, say, `parse_frontmatter` surfaces as a confusing
failure in whichever check happened to trip over it. Testing them directly makes
the failure point obvious.

Malformed input is a recurring theme: these functions must degrade to an empty
result rather than raise, because a raise becomes `check_error` (our bug) instead
of a reported gap (the agent's).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.middleware.checks import checks_for
from src.middleware.checks.common import (
    all_page_slugs,
    frontmatter_list,
    graph_edge_count,
    graph_node_ids,
    graph_nodes_missing_pages,
    index_entries,
    index_has,
    load_graph,
    parse_frontmatter,
    read_text,
    slug_of,
    wikilinks_in,
)

pytestmark = pytest.mark.unit


# --- slugs and wikilinks ----------------------------------------------------

@pytest.mark.parametrize(
    "path, expected",
    [
        ("concepts/self-attention.md", "self-attention"),
        ("wiki/entities/ashish-vaswani.md", "ashish-vaswani"),
        ("plain.md", "plain"),
    ],
)
def test_slug_of(path, expected):
    assert slug_of(path) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("see [[self-attention]]", ["self-attention"]),
        ("[[Multi Head Attention]]", ["multi-head-attention"]),   # spaces -> hyphens
        ("[[page|alias]]", ["page"]),                             # alias stripped
        ("[[page#heading]]", ["page"]),                           # anchor stripped
        ("[[a]] and [[b]]", ["a", "b"]),
        ("no links here", []),
        ("", []),
    ],
)
def test_wikilinks_in(text, expected):
    assert wikilinks_in(text) == expected


def test_wikilinks_in_ignores_html_comments():
    """SKILL.md documents the link format inside comments — those are not links."""
    text = "<!-- Format: [[page-name]] — summary -->\nReal link: [[self-attention]]"
    assert wikilinks_in(text) == ["self-attention"]


def test_all_page_slugs(tmp_path):
    (tmp_path / "concepts").mkdir()
    (tmp_path / "concepts" / "a.md").write_text("x")
    (tmp_path / "b.md").write_text("x")
    (tmp_path / "notes.txt").write_text("x")        # not markdown
    assert all_page_slugs(tmp_path) == {"a", "b"}


# --- reading ----------------------------------------------------------------

def test_read_text_returns_empty_for_missing_file(tmp_path):
    """Missing files are a gap for a check to report, never an exception."""
    assert read_text(tmp_path, "nope.md") == ""


def test_read_text_returns_empty_for_a_directory(tmp_path):
    (tmp_path / "adir").mkdir()
    assert read_text(tmp_path, "adir") == ""


# --- index.md ---------------------------------------------------------------

def test_index_entries_parses_slug_and_summary(tmp_path):
    (tmp_path / "index.md").write_text(
        "# Wiki Index\n\n"
        "## Concepts\n"
        "<!-- Format: [[page]] — summary -->\n"
        "[[self-attention]] — Attention over a single sequence\n"
        "[[positional-encoding]] - Sinusoidal position signals\n",   # ASCII dash too
        encoding="utf-8",
    )
    entries = index_entries(tmp_path)
    assert entries == {
        "self-attention": "Attention over a single sequence",
        "positional-encoding": "Sinusoidal position signals",
    }


def test_index_entries_empty_when_index_missing(tmp_path):
    assert index_entries(tmp_path) == {}


def test_index_has(tmp_path):
    (tmp_path / "index.md").write_text("[[self-attention]] — x\n", encoding="utf-8")
    assert index_has(tmp_path, "self-attention")
    assert not index_has(tmp_path, "absent")


# --- graph.json -------------------------------------------------------------

GRAPH = {
    "nodes": [
        {"id": "a", "path": "concepts/a.md"},
        {"id": "b", "path": "concepts/b.md"},
        {"id": "no-path"},
    ],
    "edges": [
        {"source": "a", "target": "b", "relation": "uses"},
        {"source": "b", "target": "a", "relation": "cites"},
    ],
}


@pytest.fixture
def graph_wiki(tmp_path: Path) -> Path:
    (tmp_path / "graph").mkdir()
    (tmp_path / "concepts").mkdir()
    (tmp_path / "concepts" / "a.md").write_text("x")
    (tmp_path / "graph" / "graph.json").write_text(json.dumps(GRAPH), encoding="utf-8")
    return tmp_path


def test_load_graph(graph_wiki):
    graph = load_graph(graph_wiki)
    assert len(graph["nodes"]) == 3
    assert len(graph["edges"]) == 2


@pytest.mark.parametrize("content", ["{not json", "", "[]", '"a string"'])
def test_load_graph_degrades_instead_of_raising(tmp_path, content):
    """A malformed graph is the agent's gap to fix, not a crash in our code."""
    (tmp_path / "graph").mkdir()
    (tmp_path / "graph" / "graph.json").write_text(content, encoding="utf-8")
    assert load_graph(tmp_path) == {"nodes": [], "edges": []}


def test_load_graph_missing_file(tmp_path):
    assert load_graph(tmp_path) == {"nodes": [], "edges": []}


def test_graph_node_ids(graph_wiki):
    assert graph_node_ids(load_graph(graph_wiki)) == {"a", "b", "no-path"}


def test_graph_edge_count_counts_both_directions(graph_wiki):
    graph = load_graph(graph_wiki)
    assert graph_edge_count(graph, "a") == 2      # one outgoing, one incoming
    assert graph_edge_count(graph, "absent") == 0


def test_graph_nodes_missing_pages(graph_wiki):
    """b.md was never created, and no-path has no path at all."""
    missing = graph_nodes_missing_pages(load_graph(graph_wiki), graph_wiki)
    assert set(missing) == {"b", "no-path"}


# --- frontmatter ------------------------------------------------------------

def test_parse_frontmatter():
    text = "---\ntitle: X\ntype: concept\ntags: [a, b]\n---\n\nbody\n"
    assert parse_frontmatter(text) == {"title": "X", "type": "concept", "tags": "[a, b]"}


@pytest.mark.parametrize(
    "text",
    [
        "no frontmatter at all",
        "---\nunterminated block\n",
        "",
    ],
)
def test_parse_frontmatter_returns_empty_when_absent_or_malformed(text):
    assert parse_frontmatter(text) == {}


@pytest.mark.parametrize(
    "value, expected",
    [
        ("[a, b]", ["a", "b"]),
        ("['quoted', \"double\"]", ["quoted", "double"]),
        ("[one]", ["one"]),
        ("[]", []),
        ("", []),
    ],
)
def test_frontmatter_list(value, expected):
    assert frontmatter_list(value) == expected


# --- registry ---------------------------------------------------------------

@pytest.mark.parametrize("path, count", [("ingest", 9), ("query", 3), ("marp", 4)])
def test_checks_for_returns_the_registered_checks(path, count):
    assert len(checks_for(path)) == count


def test_checks_for_unknown_path_is_empty_not_an_error():
    """Classification can name a path before its checks exist."""
    assert checks_for("does-not-exist") == []
