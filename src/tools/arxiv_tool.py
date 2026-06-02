import json
import re
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
import arxiv
from langchain_core.tools import tool
from src.tools.utils import get_wiki_root, norm_title, title_score

WIKI_ROOT = get_wiki_root()

RAW_PAPERS_DIR = WIKI_ROOT / "raw" / "papers"
_ARXIV_CACHE_DIR = WIKI_ROOT / ".cache" / "arxiv"

# Serialize arXiv API search calls across threads (e.g. run_gate fires cases concurrently
# via asyncio.gather → thread pool). Each arxiv.Client has its own delay_seconds counter
# so concurrent callers don't respect each other's throttle, causing 429s.
_ARXIV_LOCK = threading.Lock()


def _arxiv_cache_path(arxiv_id: str) -> Path:
    base = re.sub(r"v\d+$", "", arxiv_id.strip())
    safe = re.sub(r"[^\w.-]+", "_", base)
    return _ARXIV_CACHE_DIR / f"{safe}.json"


@tool()
def fetch_arxiv(query: str) -> dict:
    """
    Search arXiv and download the best-matching paper as a PDF.

    Accepts an arXiv ID, arXiv URL, or free-text topic/title. Results are cached
    by arXiv ID so repeat calls skip the network entirely.

    Args:
        query: arXiv ID (e.g. '1706.03762'), arXiv URL, or topic/title string
               (e.g. 'attention is all you need').

    Returns:
        dict with keys:
          - title (str): paper title
          - authors (list[str]): author names
          - pdf_path (str): absolute path to the downloaded PDF under raw/papers/
          - metadata (dict): arxiv_id, doi, published, updated, categories, url

        On failure, returns a structured error dict instead of raising:
          - {"error": "rate_limited", "suggestion": "..."} — arXiv HTTP 429
          - {"error": "not_found", "query": "..."} — no paper matched the query
    """
    arxiv_id = None
    url_match = re.search(r"arxiv\.org/(?:abs|pdf)/([0-9]+\.[0-9]+)", query)
    id_match = re.match(r"^[0-9]+\.[0-9]+(v[0-9]+)?$", query.strip())
    if url_match:
        arxiv_id = url_match.group(1)
    elif id_match:
        arxiv_id = query.strip()

    # Cache hit: skip arxiv entirely
    if arxiv_id:
        cache_path = _arxiv_cache_path(arxiv_id)
        if cache_path.is_file():
            cached = json.loads(cache_path.read_text())
            if Path(cached["pdf_path"]).is_file():
                return cached

    # 3s delay between requests + serialize across threads so concurrent callers
    # (e.g. run_gate asyncio.gather) don't fire simultaneously and trigger 429.
    # NOTE: do not patch urllib opener here — install_opener strips feedparser's
    # Accept headers, causing arXiv to return HTTP 406.
    client = arxiv.Client(page_size=10, delay_seconds=3.0, num_retries=3)

    try:
        with _ARXIV_LOCK:
            if arxiv_id:
                results = list(client.results(arxiv.Search(id_list=[arxiv_id])))
            else:
                # Try title-scoped first, only fall back to general if no good match
                title_results = list(
                    client.results(arxiv.Search(query=f'ti:"{query}"', max_results=10))
                )
                if title_results:
                    top_ratio = SequenceMatcher(
                        a=norm_title(query), b=norm_title(title_results[0].title)
                    ).ratio()
                    if top_ratio > 0.8:
                        results = title_results
                    else:
                        seen = {r.entry_id for r in title_results}
                        results = list(title_results)
                        for r in client.results(arxiv.Search(query=query, max_results=10)):
                            if r.entry_id not in seen:
                                results.append(r)
                else:
                    results = list(client.results(arxiv.Search(query=query, max_results=10)))
    except Exception as exc:
        if "429" in str(exc) or "Too Many Requests" in str(exc):
            return {
                "error": "rate_limited",
                "suggestion": "arXiv rate limit exceeded. Retry later or switch to quick ingest mode (use a cached paper ID).",
            }
        raise

    if not results:
        return {"error": "not_found", "query": query}

    paper = results[0] if arxiv_id else max(results, key=lambda r: title_score(query, r.title))

    RAW_PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", paper.title.lower()).strip("_")
    pdf_path = str(RAW_PAPERS_DIR / f"{slug}.pdf")

    # Retry PDF download — arxiv occasionally returns transient HTTP errors
    _last_exc: Exception | None = None
    for attempt in range(3):
        try:
            paper.download_pdf(filename=pdf_path)
            _last_exc = None
            break
        except Exception as exc:
            _last_exc = exc
            if attempt < 2:
                time.sleep(3 * (attempt + 1))
    if _last_exc is not None:
        raise _last_exc

    out = {
        "title": paper.title,
        "authors": [a.name for a in paper.authors],
        "pdf_path": pdf_path,
        "metadata": {
            "arxiv_id": paper.get_short_id(),
            "doi": paper.doi,
            "published": str(paper.published.date()),
            "updated": str(paper.updated.date()),
            "categories": paper.categories,
            "url": paper.entry_id,
        },
    }

    if arxiv_id:
        # Cache metadata by arXiv ID so repeat calls can skip network + re-download if PDF still exists.
        _ARXIV_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _arxiv_cache_path(arxiv_id).write_text(json.dumps(out, indent=2))

    return out

