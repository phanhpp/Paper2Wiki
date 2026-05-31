"""Tooling utilities used by Paper2Wiki agents."""

from src.tools.ingest_tools import fetch_arxiv, parse_pdf_docling, get_ingest_mode
from src.tools.wiki_integrity_check import quick_wiki_integrity_check
from src.tools.observability_eval_tools.fetch_traces import run_trace_report_async
from src.tools.observability_eval_tools.summarize_traces import summarize_traces_async
from src.tools.observability_eval_tools.anomaly_detection import detect_anomalies_async, compute_baselines_async
from src.tools.web_tools import web_search, web_extract

all_tools = [
    # Ingest
    fetch_arxiv,
    parse_pdf_docling,
    get_ingest_mode,
    # Web
    web_search,
    web_extract,
    # Wiki
    quick_wiki_integrity_check,
    # Observability / eval
    run_trace_report_async,
    summarize_traces_async,
    detect_anomalies_async,
    compute_baselines_async,
]
