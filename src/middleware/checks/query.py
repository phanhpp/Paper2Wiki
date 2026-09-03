"""Query contract — checks a run that was asked to use the wiki.

Entered from the user's *request* ("use the wiki", "what do we know about"),
never from a read: a question unrelated to the wiki reads nothing, and that is
correct behaviour rather than a failure.

A write under ``queries/`` is an optional modifier, never the trigger — an
answer is persisted only when the query is worth keeping, so not persisting is
never a failure. Once a run does persist, that page is a wiki page and meets the
same standards as any other.

The two semantic checks from the plan (are the pages read relevant to the
question; does the wiki hold pages relevant to the question) need the embedding
layer and are not implemented yet — see ``docs/loop_engineer/implement_plan.md``.
"""

from __future__ import annotations

from src.middleware.checks.common import (
    LOG_REL,
    all_page_slugs,
    index_has,
    parse_frontmatter,
    read_text,
    slug_of,
    wikilinks_in,
)
from src.middleware.types import CheckResult, RunContext

QUERY_PREFIX = "queries/"
REQUIRED_FRONTMATTER = {"title", "created", "updated", "type", "tags", "sources"}


def _saved_pages(ctx: RunContext) -> list[str]:
    return ctx.wrote_under(QUERY_PREFIX)


def answer_cites_wiki(ctx: RunContext) -> CheckResult:
    """Q1 - the answer cites at least one [[wikilink]], and they all resolve.

    Required unconditionally. A run only reaches this path because the user
    asked for the wiki, so an answer with no link either ignored the wiki or is
    claiming knowledge it cannot attribute — there is no valid third case.

    Catches the worst failure for a knowledge base: answering from model memory
    while appearing to consult the wiki.
    """
    cited = wikilinks_in(ctx.answer)
    if not cited:
        return CheckResult.fail(
            "Q1",
            "the answer cites no [[wikilink]] despite being asked to use the wiki — "
            "cite the pages you used, or say plainly that the wiki has nothing on this",
        )

    known = all_page_slugs(ctx.wiki_root)
    broken = sorted({link for link in cited if link not in known})
    if broken:
        return CheckResult.fail("Q1", f"answer cites pages that do not exist: {broken}")
    return CheckResult.ok("Q1")


def cited_pages_were_read(ctx: RunContext) -> CheckResult:
    """Q2 - the run actually opened the pages it cited.

    Reads are matched by substring because tool arguments carry virtual paths
    (``/wiki/concepts/x.md``) while slugs are bare. A citation for a page never
    opened is a fabricated attribution.

    Skipped when nothing was read at all: that is Q1's territory, and reporting
    both would blame the same failure twice.
    """
    if not ctx.reads:
        return CheckResult.ok("Q2")

    read_blob = " ".join(ctx.reads)
    unread = sorted({link for link in wikilinks_in(ctx.answer) if link not in read_blob})
    if not unread:
        return CheckResult.ok("Q2")
    return CheckResult.fail("Q2", f"cited pages that were never opened this run: {unread}")


def saved_answer_is_valid(ctx: RunContext) -> CheckResult:
    """Q3 - if the answer was persisted, it meets page standards.

    Conditional: persisting is optional. Its absence is never a failure, but a
    page that *is* written must have frontmatter, appear in ``index.md``, and be
    logged, exactly like any other wiki page.
    """
    saved = _saved_pages(ctx)
    if not saved:
        return CheckResult.ok("Q3")

    gaps: list[str] = []
    for rel in saved:
        fm = parse_frontmatter(read_text(ctx.wiki_root, rel))
        if not fm:
            gaps.append(f"{rel}: missing or malformed frontmatter block")
        else:
            missing = REQUIRED_FRONTMATTER - set(fm)
            if missing:
                gaps.append(f"{rel}: frontmatter missing {sorted(missing)}")
        if not index_has(ctx.wiki_root, slug_of(rel)):
            gaps.append(f"index.md has no entry for {slug_of(rel)}")

    if str(LOG_REL) not in ctx.writes:
        gaps.append("log.md was not appended for the saved query")

    return CheckResult.ok("Q3") if not gaps else CheckResult.fail("Q3", "; ".join(gaps))


CHECKS = [
    answer_cites_wiki,
    cited_pages_were_read,
    saved_answer_is_valid,
]
