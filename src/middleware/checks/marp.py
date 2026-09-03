"""Marp contract — checks a slide deck produced by the marp-slide-creator subagent.

Decks are built inside the Daytona sandbox and downloaded to ``marp-slides/`` on
the host (``src/agents/daytona_agent.py:download_outputs``), so that directory —
not ``wiki/`` — is what these checks inspect.

"Deck built without error", as written in ``loop_engineering.md``, is not
observable from the host: the build happens in the sandbox and no exit status
reaches us. What *is* observable is whether the downloaded file is a structurally
valid Marp deck, which is the failure that actually matters — a file that exists
but is not a deck.
"""

from __future__ import annotations

import re

from src.middleware.types import CheckResult, RunContext

# "marp: true" in the YAML frontmatter is what makes a markdown file a deck.
_MARP_DIRECTIVE_RE = re.compile(r"^\s*marp\s*:\s*true\s*$", re.IGNORECASE | re.MULTILINE)
# A slide break: "---" alone on a line.
_SLIDE_BREAK_RE = re.compile(r"^---\s*$", re.MULTILINE)
# "make me 12 slides", "a 10 slide deck"
_REQUESTED_COUNT_RE = re.compile(r"(\d+)\s*[- ]?\s*slides?\b", re.IGNORECASE)

SLIDE_COUNT_TOLERANCE = 2


def _decks(ctx: RunContext) -> list[str]:
    return [a for a in ctx.artifacts if a.endswith(".md")]


def _read(ctx: RunContext, rel: str) -> str:
    if ctx.artifacts_root is None:
        return ""
    try:
        return (ctx.artifacts_root / rel).read_text(encoding="utf-8")
    except OSError:
        return ""


def slide_count(text: str) -> int:
    """Slides in a Marp file.

    Frontmatter is delimited by the first two ``---``, so those are not slide
    breaks; every later ``---`` starts a new slide.
    """
    if not text.strip():
        return 0
    breaks = len(_SLIDE_BREAK_RE.findall(text))
    if text.lstrip().startswith("---"):
        breaks -= 2  # opening and closing frontmatter delimiters
    return max(breaks + 1, 1)


def requested_slide_count(question: str) -> int | None:
    """Slide count the user asked for, if they named one."""
    match = _REQUESTED_COUNT_RE.search(question or "")
    return int(match.group(1)) if match else None


def deck_exists(ctx: RunContext) -> CheckResult:
    """M1 - a markdown deck landed in marp-slides/.

    The subagent builds in the sandbox; if the download step is skipped the user
    is told slides were made while nothing reached the host.
    """
    if _decks(ctx):
        return CheckResult.ok("M1")
    other = [a for a in ctx.artifacts if not a.endswith(".md")]
    detail = f" (only {other} were downloaded)" if other else ""
    return CheckResult.fail("M1", f"no .md deck was saved under marp-slides/{detail}")


def deck_is_marp(ctx: RunContext) -> CheckResult:
    """M2 - each deck declares `marp: true`.

    Without the directive Marp renders it as a plain markdown document, so the
    file exists and looks right while producing no slides.
    """
    bad = [rel for rel in _decks(ctx) if not _MARP_DIRECTIVE_RE.search(_read(ctx, rel))]
    if not bad:
        return CheckResult.ok("M2")
    return CheckResult.fail("M2", f"missing `marp: true` frontmatter directive: {bad}")


def deck_has_slides(ctx: RunContext) -> CheckResult:
    """M3 - each deck has more than one slide.

    A single-slide deck means the `---` separators were omitted — the most
    common way a generated deck is structurally wrong.
    """
    thin = [rel for rel in _decks(ctx) if slide_count(_read(ctx, rel)) < 2]
    if not thin:
        return CheckResult.ok("M3")
    return CheckResult.fail("M3", f"deck has fewer than 2 slides (missing `---` breaks): {thin}")


def slide_count_matches_request(ctx: RunContext) -> CheckResult:
    """M4 - if the user named a slide count, the deck is within +/-2 of it.

    Conditional: most requests do not name a number, and inventing a target
    would fail correct decks.
    """
    wanted = requested_slide_count(ctx.question)
    if wanted is None:
        return CheckResult.ok("M4")

    gaps = [
        f"{rel}: {actual} slides, requested ~{wanted}"
        for rel in _decks(ctx)
        if abs((actual := slide_count(_read(ctx, rel))) - wanted) > SLIDE_COUNT_TOLERANCE
    ]
    if not gaps:
        return CheckResult.ok("M4")
    return CheckResult.fail("M4", "; ".join(gaps))


CHECKS = [
    deck_exists,
    deck_is_marp,
    deck_has_slides,
    slide_count_matches_request,
]
