# Paper2Wiki Agent - Development guide

## Project Structure

```
llm_wiki/                         # Paper2Wiki — repository root
├── src/
│   ├── agents/
│   │   ├── agent.py
│   ├── tools/
│   │   ├── ingest_tools.py
│   │   ├── lint.py
│   │   ├── utils.py
│   │   └── docling_parser.py
│   └── utils.py
├── skills/
│   ├── llm-wiki/                   
├── wiki/                         # wiki (index, log, papers, concepts, entities, graph)
└── AGENTS.md                    
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
