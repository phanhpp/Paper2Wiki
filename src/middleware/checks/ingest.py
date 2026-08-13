"""Ingest contract — 8 script checks, all deterministic and free.

Entered when a run wrote under ``wiki/raw/``, ``wiki/entities/`` or
``wiki/concepts/``. The two LLM judges from ``loop_engineering.md`` (is the page
grounded in the source, do the graph edges reflect real relationships) stay in
``eval/run_weekly_eval.py``: they need the whole source paper in the prompt, and
the weekly golden eval already runs them.

S5-S7 test presence and consistency, not correctness. The graph is a judgement
the agent makes during construction — node typing, edge semantics, confidence —
so there is no recomputed truth to diff against. Whether the edges are
*sensible* is the weekly judge's job.
"""

from __future__ import annotations

from pathlib import Path

from src.middleware.checks.common import (
    LOG_REL,
    all_page_slugs,
    frontmatter_list,
    graph_edge_count,
    graph_node_ids,
    graph_nodes_missing_pages,
    index_has,
    load_graph,
    parse_frontmatter,
    read_text,
    slug_of,
    wikilinks_in,
)
from src.middleware.types import CheckResult, RunContext

PAGE_PREFIXES = ("entities/", "concepts/")
REQUIRED_FRONTMATTER = {"title", "created", "updated", "type", "tags", "sources"}


def _pages(ctx: RunContext) -> list[str]:
    """Wiki pages written this run — what the per-page checks operate on."""
    return ctx.wrote_under(*PAGE_PREFIXES)


def page_written(ctx: RunContext) -> CheckResult:
    """S1 - the run produced a page under entities/ or concepts/.

    A run can reach the ingest path by writing only ``raw/`` (fetched the source,
    never wrote the page). That is the failure this catches.
    """
    if _pages(ctx):
        return CheckResult.ok("S1")
    raw = ctx.wrote_under("raw/")
    detail = f" (wrote only {', '.join(raw)})" if raw else ""
    return CheckResult.fail("S1", f"no page written under entities/ or concepts/{detail}")


def frontmatter_valid(ctx: RunContext) -> CheckResult:
    """S2 - every new page has frontmatter with the required keys."""
    gaps: list[str] = []
    for rel in _pages(ctx):
        text = read_text(ctx.wiki_root, rel)
        fm = parse_frontmatter(text)
        if not fm:
            gaps.append(f"{rel}: missing or malformed frontmatter block")
            continue
        missing = REQUIRED_FRONTMATTER - set(fm)
        if missing:
            gaps.append(f"{rel}: frontmatter missing {sorted(missing)}")
    return CheckResult.ok("S2") if not gaps else CheckResult.fail("S2", "; ".join(gaps))


def wikilinks_resolve(ctx: RunContext) -> CheckResult:
    """S3 - every [[wikilink]] on a new page resolves to a real page."""
    known = all_page_slugs(ctx.wiki_root)
    gaps: list[str] = []
    for rel in _pages(ctx):
        broken = sorted({link for link in wikilinks_in(read_text(ctx.wiki_root, rel)) if link not in known})
        if broken:
            gaps.append(f"{rel}: broken wikilinks {broken}")
    return CheckResult.ok("S3") if not gaps else CheckResult.fail("S3", "; ".join(gaps))


def index_updated(ctx: RunContext) -> CheckResult:
    """S4 - index.md lists every new page."""
    missing = [slug_of(rel) for rel in _pages(ctx) if not index_has(ctx.wiki_root, slug_of(rel))]
    if not missing:
        return CheckResult.ok("S4")
    return CheckResult.fail("S4", f"index.md has no entry for {missing}")


def log_appended(ctx: RunContext) -> CheckResult:
    """S5 - log.md was appended during this run.

    Detected from the filesystem snapshot diff, not by parsing dates: log entries
    are stamped ``YYYY-MM-DD`` with no time, so two ingests on the same day are
    indistinguishable by content alone.
    """
    if str(LOG_REL) in ctx.writes:
        return CheckResult.ok("S5")
    return CheckResult.fail("S5", "log.md was not appended this run")


def graph_has_node(ctx: RunContext) -> CheckResult:
    """S6 - graph.json has a node for each new page."""
    ids = graph_node_ids(load_graph(ctx.wiki_root))
    missing = [slug_of(rel) for rel in _pages(ctx) if slug_of(rel) not in ids]
    if not missing:
        return CheckResult.ok("S6")
    return CheckResult.fail("S6", f"graph.json has no node for {missing}")


def graph_node_connected(ctx: RunContext) -> CheckResult:
    """S7 - each new page's node has at least one edge.

    An isolated node adds nothing: the point of the graph is the relationships.
    """
    graph = load_graph(ctx.wiki_root)
    ids = graph_node_ids(graph)
    orphans = [
        slug for rel in _pages(ctx)
        if (slug := slug_of(rel)) in ids and graph_edge_count(graph, slug) == 0
    ]
    if not orphans:
        return CheckResult.ok("S7")
    return CheckResult.fail("S7", f"graph node has no edges: {orphans}")


def graph_consistent(ctx: RunContext) -> CheckResult:
    """S8 - graph nodes for pages written this run point at files that exist.

    Deliberately scoped to this run, not the whole graph. A wiki accumulates
    dangling nodes over time — entities the agent registered but never wrote a
    page for — and a whole-graph sweep would fail *every* future ingest for
    damage the current run did not cause. An agent blamed for pre-existing drift
    retries, fails again, and the loop gets switched off.

    Whole-graph consistency is a maintenance concern (the consolidation agent /
    weekly eval), not an in-run gate.
    """
    slugs = {slug_of(rel) for rel in _pages(ctx)}
    if not slugs:
        return CheckResult.ok("S8")

    graph = load_graph(ctx.wiki_root)
    missing = [
        node_id
        for node_id in graph_nodes_missing_pages(graph, ctx.wiki_root)
        if node_id in slugs
    ]
    if not missing:
        return CheckResult.ok("S8")
    return CheckResult.fail("S8", f"graph node path does not exist for {sorted(missing)}")


def sources_recorded(ctx: RunContext) -> CheckResult:
    """S9 - each new page declares a `sources` entry that exists on disk.

    The sha256 in ``raw/`` filenames is validated by ``compute_sha256`` during
    ingest; here we only confirm the page points at a source that is really
    there, which is what makes the page traceable.
    """
    gaps: list[str] = []
    for rel in _pages(ctx):
        fm = parse_frontmatter(read_text(ctx.wiki_root, rel))
        sources = frontmatter_list(fm.get("sources", ""))
        if not sources:
            gaps.append(f"{rel}: frontmatter `sources` is empty")
            continue
        # SCHEMA.md defines sources as wiki-relative paths under raw/. Anything
        # URL-shaped is left alone rather than reported as a missing file.
        paths = [s for s in sources if not s.lower().startswith(("http://", "https://"))]
        absent = [s for s in paths if not (ctx.wiki_root / s).exists()]
        if absent:
            gaps.append(f"{rel}: sources not found on disk {absent}")
    return CheckResult.ok("S9") if not gaps else CheckResult.fail("S9", "; ".join(gaps))


CHECKS = [
    page_written,
    frontmatter_valid,
    wikilinks_resolve,
    index_updated,
    log_appended,
    graph_has_node,
    graph_node_connected,
    graph_consistent,
    sources_recorded,
]
