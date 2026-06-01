"""Tooling utilities used by Paper2Wiki agents."""

from src.tools.web_tools.registry import load_config
from src.tools.wiki_integrity_check import quick_wiki_integrity_check
from src.tools.observability_eval_tools.fetch_traces import run_trace_report_async
from src.tools.observability_eval_tools.summarize_traces import summarize_traces_async
from src.tools.observability_eval_tools.anomaly_detection import detect_anomalies_async, compute_baselines_async
from src.tools.observability_eval_tools.create_eval_datasets import create_datasets_from_anomaly_report


def _build_tools() -> list:
    _config = load_config()
    _mode = _config.get("ingest", {}).get("mode", "fast").strip().lower()
    if _mode not in ("quality", "fast"):
        _mode = "fast"

    if _mode == "quality":
        from src.tools.arxiv_tool import fetch_arxiv
        from src.tools.docling_parser import parse_pdf_docling
        ingest_tools = [fetch_arxiv, parse_pdf_docling]
    else:
        from src.tools.web_tools import web_search, web_extract
        ingest_tools = [web_search, web_extract]

    return [
        *ingest_tools,
        quick_wiki_integrity_check,
        run_trace_report_async,
        summarize_traces_async,
        detect_anomalies_async,
        compute_baselines_async,
        create_datasets_from_anomaly_report,
    ]


all_tools = _build_tools()
