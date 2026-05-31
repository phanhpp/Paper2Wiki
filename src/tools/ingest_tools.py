from langchain_core.tools import tool

from src.tools.arxiv_tool import fetch_arxiv  # noqa: F401 — re-exported
from src.tools.docling_parser import parse_pdf_docling  # noqa: F401 — re-exported
from src.tools.web_tools.registry import load_config


@tool()
def get_ingest_mode() -> str:
    """
    Return the configured ingest mode for the wiki pipeline.

    Reads ingest.mode from ~/.paper2wiki/config.yaml (or PAPER2WIKI_CONFIG path).
    Returns "quality" or "fast". Defaults to "fast" when unset or invalid.

    quality = fetch_arxiv + parse_pdf_docling  (slow, best structural fidelity)
    fast    = web_extract via web tools         (faster, lower fidelity for PDFs)

    Explicit user instructions in the prompt always override this value.
    """
    config = load_config()
    mode = config.get("ingest", {}).get("mode", "fast").strip().lower()
    return mode if mode in ("quality", "fast") else "fast"
