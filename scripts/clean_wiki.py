#!/usr/bin/env python3
"""Repair wiki drift left behind by interrupted ingest runs.

Three fixes, all mechanical:

1. **Stale source extensions** - pages declaring ``raw/papers/x.pdf`` when the
   file on disk is ``raw/papers/x.md``. Ingest stores the *parsed* markdown, so
   the ``.pdf`` reference was never right.
2. **Stale graph node paths** - same defect as (1), fixed before pruning so a
   node that merely points at the wrong extension is not mistaken for dangling
   and deleted along with its edges.
3. **Dangling graph nodes** - nodes whose ``path`` points at a file that does not
   exist, left by runs that wrote graph entries but never wrote the pages. Edges
   touching a pruned node go with it, or the graph keeps references to ids that
   no longer exist.
4. **Report-only: pages with no graph node** - listed but never changed, since
   inventing edges is a judgement call for the agent, not a script.

Nothing here writes pages or invents content. Pruning a node discards the record
that a paper was partially processed; the source is still in ``raw/``, so a
later ingest re-creates the node properly.

    python scripts/clean_wiki.py            # dry run, prints the diff
    python scripts/clean_wiki.py --apply    # write the changes
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.middleware.checks.common import (  # noqa: E402
    GRAPH_REL,
    PAGE_DIRS,
    frontmatter_list,
    parse_frontmatter,
)
from src.tools.utils import get_wiki_root  # noqa: E402


def page_files(wiki_root: Path) -> list[Path]:
    return [p for d in PAGE_DIRS for p in (wiki_root / d).rglob("*.md") if p.is_file()]


def fix_stale_sources(wiki_root: Path, apply: bool) -> list[str]:
    """Repoint `sources:` entries at the file that actually exists.

    Only rewrites when the *same stem* exists with a different extension — never
    guesses at an unrelated file.
    """
    changes: list[str] = []
    for page in page_files(wiki_root):
        text = page.read_text(encoding="utf-8")
        sources = frontmatter_list(parse_frontmatter(text).get("sources", ""))
        new_text = text
        for src in sources:
            if (wiki_root / src).exists():
                continue
            candidates = sorted((wiki_root / src).parent.glob(Path(src).stem + ".*"))
            if len(candidates) != 1:
                continue
            replacement = str(candidates[0].relative_to(wiki_root))
            new_text = new_text.replace(src, replacement)
            changes.append(f"{page.relative_to(wiki_root)}: {src} -> {replacement}")
        if new_text != text and apply:
            page.write_text(new_text, encoding="utf-8")
    return changes


def repair_graph(wiki_root: Path, apply: bool) -> tuple[list[str], list[str], list[str]]:
    """Repair then prune the graph, in one pass over one in-memory copy.

    Order matters, and a dry run must show the same result as ``--apply``:
    the ``attention_is_all_you_need`` node points at a ``.pdf`` while the file on
    disk is ``.md``. Repair it first and it survives; treat it as dangling and
    pruning deletes seven good edges linking the paper to the concepts it
    introduced. Doing both against a single loaded graph keeps the preview
    honest.

    Returns ``(repaired_paths, pruned_nodes, dropped_edges)``.
    """
    graph_path = wiki_root / GRAPH_REL
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    nodes = [n for n in (graph.get("nodes") or []) if isinstance(n, dict)]
    edges = graph.get("edges") or []

    # 1. repoint nodes whose file exists under a different extension
    repaired: list[str] = []
    for node in nodes:
        rel = node.get("path")
        if not rel or (wiki_root / rel).exists():
            continue
        candidates = sorted((wiki_root / rel).parent.glob(Path(rel).stem + ".*"))
        if len(candidates) != 1:
            continue  # ambiguous — leave it for a human
        replacement = str(candidates[0].relative_to(wiki_root))
        repaired.append(f"{node.get('id')}: {rel} -> {replacement}")
        node["path"] = replacement

    # 2. prune what is still dangling, plus every edge that touches it
    dangling = {
        n["id"] for n in nodes
        if n.get("id") and (not n.get("path") or not (wiki_root / n["path"]).exists())
    }
    dropped_edges = [
        f"{e.get('source')} --{e.get('relation')}--> {e.get('target')}"
        for e in edges
        if e.get("source") in dangling or e.get("target") in dangling
    ]

    if apply and (repaired or dangling):
        graph["nodes"] = [n for n in nodes if n.get("id") not in dangling]
        graph["edges"] = [
            e for e in edges
            if e.get("source") not in dangling and e.get("target") not in dangling
        ]
        graph_path.write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")

    return repaired, sorted(dangling), dropped_edges


def pages_without_nodes(wiki_root: Path) -> list[str]:
    graph = json.loads((wiki_root / GRAPH_REL).read_text(encoding="utf-8"))
    ids = {n.get("id") for n in (graph.get("nodes") or []) if isinstance(n, dict)}
    return sorted(
        str(p.relative_to(wiki_root)) for p in page_files(wiki_root) if p.stem not in ids
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    args = parser.parse_args()

    wiki_root = get_wiki_root()
    mode = "APPLYING" if args.apply else "DRY RUN — nothing written"
    print(f"wiki: {wiki_root}\nmode: {mode}\n")

    sources = fix_stale_sources(wiki_root, args.apply)
    print(f"1. stale source references ({len(sources)})")
    for line in sources:
        print(f"     {line}")

    paths, nodes, edges = repair_graph(wiki_root, args.apply)
    print(f"\n2. stale graph node paths, repaired not pruned ({len(paths)})")
    for line in paths:
        print(f"     {line}")

    print(f"\n3. dangling graph nodes ({len(nodes)}) and their edges ({len(edges)})")
    for node in nodes:
        print(f"     node  {node}")
    for edge in edges:
        print(f"     edge  {edge}")

    orphans = pages_without_nodes(wiki_root)
    print(f"\n4. pages with no graph node ({len(orphans)}) — reported only, not changed")
    for page in orphans:
        print(f"     {page}")

    if not args.apply and (sources or paths or nodes):
        print("\nRe-run with --apply to write these changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
