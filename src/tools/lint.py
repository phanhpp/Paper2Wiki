#!/usr/bin/env python3
"""
lint.py — Structural checker for the Paper2Wiki wiki directory.

Usage:
  python src/tools/lint.py                        # full wiki
  python src/tools/lint.py wiki/papers/x.md       # scoped to one file

Wiki is always at <repo_root>/wiki/.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from langchain_core.tools import tool

REPO_ROOT = Path(__file__).resolve().parents[2]
WIKI_DIR = (REPO_ROOT / "wiki").resolve()

REQUIRED_FRONTMATTER = {"title", "type", "date", "slug"}

REQUIRED_BY_TYPE = {
    "paper":   {"title", "type", "date", "slug", "authors", "arxiv_id"},
    "concept": {"title", "type", "date", "slug"},
    "entity":  {"title", "type", "date", "slug"},
}

errors = []
warnings = []


def err(path, msg):
    errors.append(f"ERROR  {path}: {msg}")


def warn(path, msg):
    warnings.append(f"WARN   {path}: {msg}")


def parse_frontmatter(text: str) -> dict:
    """
    Extract YAML frontmatter from markdown text.

    Args:
        text: full file contents, expected to start with '---'

    Returns:
        dict of key→value strings, or {} if no valid frontmatter block found
    """
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


def check_file(md_file: Path, all_slugs: set[str]) -> None:
    """
    Validate a single wiki markdown file.

    Checks:
      - frontmatter block exists and is well-formed
      - all required base fields present
      - type-specific required fields present
      - slug matches filename stem
      - [[wikilinks]] resolve to known slugs
      - relative image paths exist on disk

    Args:
        md_file: absolute path to the .md file
        all_slugs: set of all .md stems across the entire wiki (for wikilink resolution)
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

    page_type = fm.get("type", "")
    if page_type in REQUIRED_BY_TYPE:
        type_missing = REQUIRED_BY_TYPE[page_type] - set(fm.keys())
        if type_missing:
            err(md_file, f"missing fields for type={page_type}: {sorted(type_missing)}")
    elif page_type == "":
        err(md_file, "frontmatter 'type' is empty")

    fm_slug = fm.get("slug", "")
    if fm_slug and fm_slug != md_file.stem:
        err(md_file, f"slug mismatch: frontmatter='{fm_slug}' filename='{md_file.stem}'")

    text_no_comments = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)
    for link in re.findall(r'\[\[([^\]|#]+)', text_no_comments):
        slug = link.strip().lower().replace(" ", "-")
        if slug not in all_slugs:
            err(md_file, f"broken wikilink: [[{link}]]")

    for img_path in re.findall(r'!\[.*?\]\(([^)]+)\)', text):
        if img_path.startswith("http"):
            continue
        if not (md_file.parent / img_path).resolve().exists():
            err(md_file, f"broken image path: {img_path}")

    if not re.findall(r'^##+ .+', text, re.MULTILINE):
        warn(md_file, "no sections found (no ## headings)")


def check_index(all_slugs: set[str], wiki_root: Path) -> None:
    """
    Verify that every [[wikilink]] in index.md points to a real page.

    Args:
        all_slugs: set of all .md stems across the wiki
        wiki_root: absolute path to the wiki directory
    """
    index = wiki_root / "index.md"
    if not index.exists():
        err(index, "index.md missing")
        return
    text_no_comments = re.sub(r'<!--.*?-->', '', index.read_text(encoding="utf-8"), flags=re.DOTALL)
    for link in re.findall(r'\[\[([^\]|#]+)', text_no_comments):
        slug = link.strip().lower().replace(" ", "-")
        if slug not in all_slugs:
            err(index, f"index points to missing page: [[{link}]]")


def check_log(wiki_root: Path) -> None:
    """
    Warn if wiki/log.md is missing (log is append-only, never auto-created).

    Args:
        wiki_root: absolute path to the wiki directory
    """
    if not (wiki_root / "log.md").exists():
        warn(wiki_root / "log.md", "log.md missing")


def run_lint(files: list[Path] | None = None) -> str:
    """
    Run lint checks against the wiki.

    Args:
        files: real absolute paths to check. None = entire wiki under WIKI_DIR,
               including index.md and log.md checks.

    Returns:
        'lint: OK' if no issues, otherwise a summary string listing
        error and warning counts with their messages.
    """
    global errors, warnings
    errors = []
    warnings = []

    all_slugs = {p.stem for p in WIKI_DIR.rglob("*.md")}

    if files is None:
        target_files = [p for p in WIKI_DIR.rglob("*.md") if p.name not in ("index.md", "log.md")]
        check_index(all_slugs, WIKI_DIR)
        check_log(WIKI_DIR)
    else:
        target_files = files

    for md_file in target_files:
        check_file(md_file, all_slugs)

    if not errors and not warnings:
        return "lint: OK"
    if errors and not warnings:
        return f"lint: {len(errors)} error(s): {errors}"
    if not errors and warnings:
        return f"lint: {len(warnings)} warning(s): {warnings}"
    return f"lint: {len(errors)} error(s): {errors}, {len(warnings)} warning(s): {warnings}"

@tool
def lint_check(files: list[str] | None = None) -> str:
    """
    Agent-facing entry point. Translates virtual paths to real paths, then runs lint.

    The agent operates in virtual path space (/wiki/papers/foo.md). This function
    rewrites those to real paths under WIKI_DIR before calling run_lint.

    Args:
        files: list of virtual paths (/wiki/...) or real absolute paths to check.
               None = full wiki scan.

    Returns:
        'lint: OK' if no issues, otherwise a summary string listing
        error and warning counts with their messages.
    """
    if files is None:
        return run_lint()

    real_files = []
    for f in files:
        s = str(f)
        if s.startswith("/wiki/"):
            real_files.append(WIKI_DIR / s.removeprefix("/wiki/"))
        else:
            real_files.append(Path(s))

    return run_lint(files=real_files)


def main():
    """CLI entry point: no args = full wiki, args = scoped file list."""
    files = [Path(f) for f in sys.argv[1:]] if len(sys.argv) > 1 else None
    summary = run_lint(files=files)

    for w in warnings:
        print(w)
    for e in errors:
        print(e)

    print(f"\n{summary}")
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
