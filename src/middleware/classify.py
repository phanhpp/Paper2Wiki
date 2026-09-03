"""Which contracts a run should be held to.

Classify on what the run **did**, never on what the user typed — a rubric fires
only when the run made a claim that can be checked, so plain chat is ungraded by
construction.

Returns a *list*: "ingest this paper and make slides" genuinely satisfies two
contracts, and picking one by precedence would silently skip the other's checks.
"""

from __future__ import annotations

from src.tools.utils import norm_title

INGEST_DIRS = ("raw/", "entities/", "concepts/")
MARP_DIRS = ("marp-slides/",)
MARP_TOOLS = frozenset({"marp-slide-creator", "task"})

# Phrasings that make the wiki obligatory. Answering a general-knowledge
# question from model knowledge is fine even when a matching page exists —
# "what is an attention mechanism?" need not cite attention-mechanism.md.
WIKI_REQUEST_PHRASES = (
    "use the wiki",
    "check the wiki",
    "search the wiki",
    "in our wiki",
    "from the wiki",
    "our notes",
    "check our notes",
    "what do we know about",
    "what have we learned",
    "existing files",
    "existing pages",
    "our knowledge",
    "knowledge base",
    "we have ingested",
    "papers we have ingested",
    "already ingested",
)


def asked_for_wiki(question: str) -> bool:
    """True when the user asked for the wiki / existing notes to be used.

    Normalised substring containment, not a similarity score: the question is
    long and the reference phrase short, so whole-string scorers (including
    ``title_score``, which exists for arXiv title matching) rate a clear match
    poorly. Normalising first makes punctuation and spacing irrelevant.
    """
    if not question:
        return False
    haystack = norm_title(question)
    return any(norm_title(phrase) in haystack for phrase in WIKI_REQUEST_PHRASES)


def classify(
    writes: list[str],
    reads: list[str],
    tools: list[str],
    question: str,
    artifacts: list[str] | None = None,
) -> list[str]:
    """Decides whether this run was an ingest, a query, a marp deck, or none — from which files it wrote and whether the user asked for the wiki

    ``writes`` are wiki-relative paths from the wiki snapshot diff and
    ``artifacts`` are paths from the ``marp-slides/`` diff — both from the
    filesystem, so every write is included however it was made — the shell, the
    sandbox download tool, or a tool we have never heard of.
    """
    paths: list[str] = []

    if artifacts or (set(tools) & MARP_TOOLS):
        paths.append("marp")

    # Writes are the ONLY ingest trigger. Tool names deliberately are not:
    # a run that called fetch_arxiv and wrote nothing is usually correct — the
    # paper was already ingested and the agent rightly skipped it. Triggering on
    # tools would fail every deliberate re-ingest.
    if any(w.startswith(INGEST_DIRS) for w in writes):
        paths.append("ingest")

    if asked_for_wiki(question):
        paths.append("query")

    return paths
