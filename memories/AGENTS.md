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
source .venv/bin/activate   
```

or use `uv`, e.g. `uv run ...`

## Known pitfalls

**DO NOT skip `lstrip('\n')` when computing sha256**
Raw-body hash is `hashlib.sha256(body.lstrip('\n').encode('utf-8')).hexdigest()` where `body` is everything after the closing `---` delimiter. Omitting the strip produces a mismatched hash.

**DO NOT omit `error=True` when the user asks about errors/failures**
When user says "analyze errors/failures/traces with errors", pass `error=True` to `run_trace_report_async`. Omitting it fetches all traces, not just failing ones.

**DO NOT leave offloaded trace files in the repo**
After trace analysis is complete and results are presented, delete the offloaded `traces_path` JSON file. Leaving it behind pollutes the repo.

**DO NOT treat `CancelledError` in traces as a code bug**
`CancelledError` usually means the user manually cancelled execution. Check trace context before proposing fixes.

**DO NOT skip reading the relevant skill before starting a task**
Always load the skill first — e.g. before any wiki job, read `skills/llm-wiki/SKILL.md`; before trace analysis, read `skills/trace-analysis/SKILL.md`.

**DO NOT re-fetch a source that is already in `raw/`**
When resuming a wiki job, the parsed source file is already in `raw/`. Use it as the source of truth; do not call web tools again unless the user explicitly requests a refresh.

**DO NOT exceed hard limits for wiki creation**
Follow the hard limits of 2 entities and 4 concepts in `skills/llm-wiki/SKILL.md`.
