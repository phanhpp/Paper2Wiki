# Paper2Wiki Agent - Development guide

## Project Structure

```markdown
llm_wiki/                         # Paper2Wiki — repository root
├── memories/
│   ├── AGENTS.md                 # long-lived agent guidance (this file)
│   └── agents_md_template.md
├── notebooks/
│   └── explore.ipynb
├── marp-slides/                  # generated Marp decks (outputs)
├── src/
│   ├── prompts/
│   │   └── system_prompt.py
│   ├── agents/
│   │   ├── agent.py              # supervisor factory (main entry)
│   │   ├── stream.py             # streaming runner + HITL interrupt handling
│   │   ├── daytona_agent.py       # Daytona-backed subagent factory
│   │   ├── sandbox_utils.py       # sandbox registry/inspection helpers
│   │   ├── backend_wrapper.py     # guarded local shell backend
│   │   └── prompt_builder.py
│   ├── tools/
│   │   ├── ingest_tools.py
│   │   ├── docling_parser.py
│   │   ├── arxiv_tool.py
│   │   ├── fetch_traces.py
│   │   ├── summarize_traces.py
│   │   ├── wiki_integrity_check.py
│   │   ├── sandbox_tools.py
│   │   ├── trace_report_pickle_cache.py  # dev-only
│   │   └── utils.py
│   └── utils.py
├── skills/
│   ├── llm-wiki/
│   ├── marp-slide/
│   └── trace-analysis/
├── wiki/                         # wiki (index, log, papers, concepts, entities, graph)
├── pyproject.toml
└── README
```

## Dev Environment

```bash
# Prefer .venv; fall back to venv if that's what your checkout has.
source .venv/bin/activate   # or: source venv/bin/activate
```

## Known pitfalls

- sha256 re-computation needs `body.lstrip('\n')` to match the stored value - hashlib.sha256(body.lstrip('\n').encode('utf-8')).hexdigest() where body = everything after the closing --- delimiter
- trace-analysis skill: when user says "analyze errors/failures/traces with errors", MUST pass `error=True` to `run_trace_report_async` — do NOT omit it and fetch all traces
- trace-analysis skill: MUST clean up offloaded trace files (remove `traces_path`) after analysis is complete and results are presented. Do NOT leave offloaded JSON files in the repo
- CancelledError in traces usually indicates user manually cancelled execution, not an actual tool/code error. Check trace context before proposing fixes.
