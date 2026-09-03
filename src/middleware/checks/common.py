"""Wiki file parsing shared across checks.

Pure stdlib + ``src.tools`` helpers — no LangChain, no network, so every
function here is directly unit-testable against a temp wiki.

Layout these functions assume (see ``wiki/SCHEMA.md`` and
``skills/llm-wiki/SKILL.md``)::

    wiki/
      index.md              "[[slug]] - one-line summary" under "## Entities" etc.
      log.md                "## [YYYY-MM-DD] action | subject", append-only
      graph/graph.json      {"nodes": [...], "edges": [...]}
      entities/  concepts/  comparisons/  queries/  papers/   *.md pages
      raw/                  source documents
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Directories whose *.md files count as wiki pages.
PAGE_DIRS = ("papers", "concepts", "entities", "comparisons", "queries")

GRAPH_REL = Path("graph") / "graph.json"
INDEX_REL = Path("index.md")
LOG_REL = Path("log.md")

# "[[slug]]" or "[[slug|alias]]" or "[[slug#heading]]"
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", flags=re.DOTALL)


def slug_of(path: str | Path) -> str:
    """Page slug for a wiki path — the filename without its extension."""
    return Path(path).stem


def wikilinks_in(text: str) -> list[str]:
    """Every ``[[wikilink]]`` target in ``text``, normalised to slug form.

    HTML comments are stripped first: templates in ``SKILL.md`` document the
    link format inside comments, and those are not real links.
    """
    body = _HTML_COMMENT_RE.sub("", text)
    return [m.strip().lower().replace(" ", "-") for m in _WIKILINK_RE.findall(body)]


def all_page_slugs(wiki_root: Path) -> set[str]:
    """Every slug that a wikilink could legitimately resolve to."""
    return {p.stem for p in wiki_root.rglob("*.md")}


def read_text(wiki_root: Path, rel: str | Path) -> str:
    """File contents, or "" when absent — checks report missing files as gaps."""
    target = wiki_root / rel
    try:
        return target.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError):
        return ""


# --- index.md ---------------------------------------------------------------

def index_entries(wiki_root: Path) -> dict[str, str]:
    """``{slug: summary}`` for every entry listed in ``index.md``.

    Entries look like ``[[positional-encoding]] - Sinusoidal position signals...``
    Summary may be empty; the slug is what membership checks care about, and the
    summary is what the semantic layer embeds.
    """
    entries: dict[str, str] = {}
    for line in read_text(wiki_root, INDEX_REL).splitlines():
        line = line.strip()
        if not line.startswith("[["):
            continue
        links = wikilinks_in(line)
        if not links:
            continue
        # Everything after the closing "]]" is the summary, minus the dash.
        _, _, tail = line.partition("]]")
        entries[links[0]] = tail.lstrip(" -—–\t")
    return entries


def index_has(wiki_root: Path, slug: str) -> bool:
    return slug in index_entries(wiki_root)


# --- graph/graph.json -------------------------------------------------------

def load_graph(wiki_root: Path) -> dict[str, Any]:
    """Parsed ``graph/graph.json``, or empty nodes/edges if missing or invalid.

    A malformed graph is reported by the checks as a gap, not raised here —
    ``check_error`` is reserved for bugs in our own code.
    """
    raw = read_text(wiki_root, GRAPH_REL)
    if not raw.strip():
        return {"nodes": [], "edges": []}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"nodes": [], "edges": []}
    if not isinstance(data, dict):
        return {"nodes": [], "edges": []}
    return {
        "nodes": data.get("nodes") or [],
        "edges": data.get("edges") or [],
    }


def graph_node_ids(graph: dict[str, Any]) -> set[str]:
    return {n["id"] for n in graph["nodes"] if isinstance(n, dict) and "id" in n}


def graph_edge_count(graph: dict[str, Any], node_id: str) -> int:
    """Edges incident to ``node_id`` in either direction."""
    return sum(
        1
        for e in graph["edges"]
        if isinstance(e, dict) and node_id in (e.get("source"), e.get("target"))
    )


def graph_nodes_missing_pages(graph: dict[str, Any], wiki_root: Path) -> list[str]:
    """Node ids whose ``path`` does not exist on disk.

    ``source-doc`` nodes point at ``raw/`` documents rather than markdown pages,
    so this checks real paths rather than page slugs.
    """
    missing: list[str] = []
    for node in graph["nodes"]:
        if not isinstance(node, dict):
            continue
        rel = node.get("path")
        if not rel:
            missing.append(str(node.get("id", "<no id>")))
        elif not (wiki_root / rel).exists():
            missing.append(str(node.get("id", "<no id>")))
    return missing


# --- frontmatter ------------------------------------------------------------

def parse_frontmatter(text: str) -> dict[str, str]:
    """YAML frontmatter as a flat ``{key: raw_value}`` map, or ``{}``.

    Mirrors ``src/tools/wiki_integrity_check.py:parse_frontmatter`` rather than
    pulling in a YAML dependency — values stay as raw strings, which is all the
    checks need.
    """
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    out: dict[str, str] = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            out[key.strip()] = value.strip()
    return out


def frontmatter_list(value: str) -> list[str]:
    """Parse a ``[a, b, c]`` frontmatter list into its items."""
    inner = value.strip().strip("[]").strip()
    if not inner:
        return []
    return [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
