from src.tools.docling_parser import parse_pdf_docling
from src.tools.arxiv_tool import fetch_arxiv
from src.tools.wiki_integrity_check import quick_wiki_integrity_check

# Re-export parser-backed tools so callers import only from this module
all_tools = [
    fetch_arxiv,
    parse_pdf_docling,
    quick_wiki_integrity_check,
]

