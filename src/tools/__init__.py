"""Tooling utilities used by Any2Wiki agents."""

from src.ingest_mode import get_ingest_mode
from src.tools.hash_tools import compute_sha256
from src.tools.wiki_integrity_check import quick_wiki_integrity_check
from src.tools.observability_eval_tools.fetch_traces import run_trace_report_async
from src.tools.observability_eval_tools.summarize_traces import summarize_traces_async
from src.tools.observability_eval_tools.anomaly_detection import detect_anomalies_async, compute_baselines_async
from src.tools.observability_eval_tools.create_eval_datasets import create_datasets_from_anomaly_report


def _build_tools() -> list:
    _mode = get_ingest_mode()

    from src.tools.web_tools import web_search, web_extract
    ingest_tools = [web_search, web_extract]

    if _mode == "quality":
        from src.tools.arxiv_tool import fetch_arxiv
        from src.tools.docling_parser import parse_pdf_docling
        ingest_tools.extend([fetch_arxiv, parse_pdf_docling])

    return [
        *ingest_tools,
        compute_sha256,
        quick_wiki_integrity_check,
        run_trace_report_async,
        summarize_traces_async,
        detect_anomalies_async,
        compute_baselines_async,
        create_datasets_from_anomaly_report,
    ]


all_tools = _build_tools()
