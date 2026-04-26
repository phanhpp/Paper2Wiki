#!/usr/bin/env python3
"""
wiki_integrity_check.py — quick wiki integrity checks.

Only checks:
- Frontmatter validation: required fields + basic formatting
- Broken wikilinks: every `[[link]]` must resolve to an existing `.md` stem

CLI:
  python src/tools/wiki_integrity_check.py            # scan every page under <repo_root>/wiki/
  python src/tools/wiki_integrity_check.py path.md    # scan only the given files

Exit code: 1 if any errors, else 0 (warnings do not fail).
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from langchain_core.tools import tool
from src.tools.utils import get_wiki_root

# Wiki vault root (env `WIKI_PATH` override, else `<repo_root>/wiki`)
WIKI_ROOT = get_wiki_root()

ALLOWED_TYPES = {"entity", "concept", "comparison", "query", "summary"}
REQUIRED_FRONTMATTER = {"title", "created", "updated", "type", "tags", "sources"}

# Directories treated as "wiki pages" when scanning the whole vault.
PAGE_DIRS = ("papers", "concepts", "entities", "comparisons", "queries")

errors = []
warnings = []


def err(path, msg):
    errors.append(f"ERROR  {path}: {msg}")


def warn(path, msg):
    warnings.append(f"WARN   {path}: {msg}")


def parse_frontmatter(text: str) -> dict:
    """Extract YAML frontmatter from markdown text."""
    if not text.startswith("---"):
        return {}
    end = text.find("---", 3)
    if end == -1:
        return {}
    fm = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fm[k.strip()] = v.strip()
    return fm


def _is_iso_date(value: str) -> bool:
    """Return True iff `value` parses as YYYY-MM-DD (quotes tolerated)."""
    try:
        datetime.strptime(value.strip().strip("'\""), "%Y-%m-%d")
        return True
    except ValueError:
        return False


def check_file(md_file: Path, all_slugs: set[str]) -> None:
    """
    Validate a single wiki markdown file.

    Checks:
      - YAML frontmatter exists and contains required fields
      - basic validation of dates/types/tags/sources
      - [[wikilinks]] resolve to known slugs
    """
    text = md_file.read_text(encoding="utf-8")

    if not text.startswith("---"):
        err(md_file, "missing frontmatter block")
        return

    fm = parse_frontmatter(text)
    if not fm:
        err(md_file, "malformed frontmatter block")
        return

    missing = REQUIRED_FRONTMATTER - set(fm.keys())
    if missing:
        err(md_file, f"missing frontmatter fields: {sorted(missing)}")

    page_type = (fm.get("type", "") or "").strip()
    if not page_type:
        err(md_file, "frontmatter 'type' is empty")
    elif page_type not in ALLOWED_TYPES:
        err(md_file, f"invalid type: {page_type!r} (expected one of {sorted(ALLOWED_TYPES)})")

    created = (fm.get("created", "") or "").strip()
    updated = (fm.get("updated", "") or "").strip()
    if created and not _is_iso_date(created):
        err(md_file, f"invalid created date: {created!r} (expected YYYY-MM-DD)")
    if updated and not _is_iso_date(updated):
        err(md_file, f"invalid updated date: {updated!r} (expected YYYY-MM-DD)")

    tags = (fm.get("tags", "") or "").strip()
    if not tags.startswith("[") or not tags.endswith("]"):
        err(md_file, "tags must be a list (e.g. tags: [foo, bar])")

    sources = (fm.get("sources", "") or "").strip()
    if not sources.startswith("[") or not sources.endswith("]"):
        err(md_file, "sources must be a list (e.g. sources: [raw/papers/x.pdf])")

    text_no_comments = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    for link in re.findall(r"\[\[([^\]|#]+)", text_no_comments):
        slug = link.strip().lower().replace(" ", "-")
        if slug not in all_slugs:
            err(md_file, f"broken wikilink: [[{link}]]")


def _resolve_page_files_for_full_scan() -> list[Path]:
    """Return the list of wiki page files for a whole-wiki scan."""
    target_files: list[Path] = []
    for d in PAGE_DIRS:
        root = WIKI_ROOT / d
        if root.is_dir():
            target_files.extend(p for p in root.rglob("*.md") if p.is_file())
    return target_files


def run_wiki_integrity_check(files: list[Path] | None = None) -> str:
    """
    Run quick integrity checks against the wiki (core implementation).

    Args:
        files: real paths to check. None = scan all wiki page directories under WIKI_ROOT.
    """
    global errors, warnings
    errors = []
    warnings = []

    all_slugs = {p.stem for p in WIKI_ROOT.rglob("*.md")}

    if files is None:
        target_files = _resolve_page_files_for_full_scan()
    else:
        target_files = files

    for md_file in target_files:
        check_file(md_file, all_slugs)

    if not errors and not warnings:
        return "wiki-check: OK"
    if errors and not warnings:
        return f"wiki-check: {len(errors)} error(s): {errors}"
    if not errors and warnings:
        return f"wiki-check: {len(warnings)} warning(s): {warnings}"
    return f"wiki-check: {len(errors)} error(s): {errors}, {len(warnings)} warning(s): {warnings}"


@tool
def quick_wiki_integrity_check(files: list[str] | None = None) -> str:
    """
    Quick check for broken wikilinks + frontmatter/tag errors after ingestion.

    Args:
        files: list of virtual paths (/wiki/...) or real absolute paths to check.
               None (or omitted) = scan the whole wiki for these quick checks.
    """
    if files is None:
        return run_wiki_integrity_check()

    real_files = []
    for f in files:
        s = str(f)
        if s.startswith("/wiki/"):
            real_files.append(WIKI_ROOT / s.removeprefix("/wiki/"))
        else:
            real_files.append(Path(s))

    return run_wiki_integrity_check(files=real_files)


def main():
    """CLI entry point: no args = whole wiki, args = scoped file list."""
    files = [Path(f) for f in sys.argv[1:]] if len(sys.argv) > 1 else None
    summary = run_wiki_integrity_check(files=files)

    for w in warnings:
        print(w)
    for e in errors:
        print(e)

    print(f"\n{summary}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
