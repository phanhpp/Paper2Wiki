import re
import arxiv
import json
from difflib import SequenceMatcher
from pathlib import Path
from src.tools.parsers.docling_parser import parse_pdf_docling
from src.tools.lint import lint_check
from src.tools.utils import norm_title, title_score
from langchain_core.tools import tool

# Resolve all filesystem outputs relative to the repo root (not the process CWD),
# so calling from notebooks/ or other working directories still writes into the clone.
REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_PAPERS_DIR = REPO_ROOT / "raw" / "papers"
RAW_ASSETS_DIR = REPO_ROOT / "raw" / "assets"
_ARXIV_CACHE_DIR = REPO_ROOT / ".cache" / "arxiv"

def _arxiv_cache_path(arxiv_id: str) -> Path:
    base = re.sub(r"v\d+$", "", arxiv_id.strip())
    safe = re.sub(r"[^\w.-]+", "_", base)
    return _ARXIV_CACHE_DIR / f"{safe}.json"

@tool
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

    # Be kind to arxiv: 3s between requests, retry on transient errors
    client = arxiv.Client(page_size=10, delay_seconds=3.0, num_retries=2)

    if arxiv_id:
        results = list(client.results(arxiv.Search(id_list=[arxiv_id])))
    else:
        # Try title-scoped first, only fall back to general if no good match
        title_results = list(client.results(
            arxiv.Search(query=f'ti:"{query}"', max_results=10)
        ))
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

    if not results:
        raise ValueError(f"No arXiv paper found for: {query}")

    paper = (
        results[0] if arxiv_id
        else max(results, key=lambda r: title_score(query, r.title))
    )

    RAW_PAPERS_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "_", paper.title.lower()).strip("_")
    pdf_path = str(RAW_PAPERS_DIR / f"{slug}.pdf")
    paper.download_pdf(filename=pdf_path)

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
        _ARXIV_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _arxiv_cache_path(arxiv_id).write_text(json.dumps(out, indent=2))

    return out




# Re-export parser-backed tools so callers import only from this module
all_tools = [
    fetch_arxiv,
    parse_pdf_docling,
    lint_check,
]

