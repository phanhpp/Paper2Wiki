from src.tools.docling_parser import parse_pdf_docling
from src.tools.arxiv_tool import fetch_arxiv
from src.tools.wiki_integrity_check import quick_wiki_integrity_check
from src.tools.fetch_traces import run_trace_report_async
from src.tools.summarize_traces import summarize_traces_async
from src.tools.anomaly_detection import detect_anomalies_async, compute_baselines_async
# Re-export parser-backed tools so callers import only from this module
all_tools = [
    fetch_arxiv,
    parse_pdf_docling,
    quick_wiki_integrity_check,
    run_trace_report_async,
    summarize_traces_async,
    detect_anomalies_async,
    compute_baselines_async,
]

