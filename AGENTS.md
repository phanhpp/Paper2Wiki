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
