## Project Overview

Paper2Wiki ingests papers into a compounding wiki knowledge base.
Stack: Deep Agents SDK, LangSmith, Daytona, Anthropic.

## Project Structure

```
llm_wiki/                         # Paper2Wiki — repository root
├── src/
│   ├── agents/
│   │   ├── agent.py
│   │   └── prompt_builder.py
│   ├── prompts/
│   │   └── system_prompt.py
│   ├── tools/
│   │   ├── ingest_tools.py
│   │   ├── lint.py
│   │   ├── utils.py
│   │   └── parsers/
│   │       ├── docling_parser.py
│   └── utils.py
├── skills/
│   ├── common/                   # graph-format, index-log-format, page-templates
│   ├── paper-ingestion/SKILL.md
│   ├── lint-fix/SKILL.md
│   └── hermes-llm-wiki.md
├── wiki/                         # Obsidian wiki (index, log, papers, concepts, entities, graph)
├── raw/                          # Source PDFs under papers/; extracted text under assets/
├── notebooks/
│   └── explore.ipynb
└── memories/                     # AGENTS.md, FULL_AGENTS.md
```

## Dev Environment

```bash
# Prefer .venv; fall back to venv if that's what your checkout has.
source .venv/bin/activate   # or: source venv/bin/activate
```