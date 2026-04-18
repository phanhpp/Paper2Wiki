# Paper2Wiki

A self-improving LLM knowledge base built on the [Karpathy LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f), powered by the [Deep Agents SDK](https://github.com/langchain-ai/deepagents).

Upload research papers → agent builds and maintains a structured, interlinked wiki that compounds knowledge over time. Unlike RAG (which re-derives knowledge on every query), the wiki grows richer with every paper added.

---

## What It Does

- **Ingest** papers (PDF, arXiv URL, DOI) → structured wiki pages with [[wikilinks]], concept pages, author profiles, citation graphs
- **Query** the compiled wiki → synthesized answers that get filed back as new wiki pages
- **Code** tasks from paper content → implement algorithms, verify benchmarks, generate mermaid diagrams (sandbox-isolated, HITL approval)
- **Self-improve** → trace-analyzer skill rewrites agent skills based on LangSmith failure traces

---

## Architecture

```
Supervisor Agent
├── Ingest Subagent     → papers → wiki pages (StoreBackend)
├── Code Subagent       → user coding tasks (SandboxBackend + HITL)
└── Query Subagent      → questions → answers from wiki

Skills (on-demand):
  paper-ingestion, wiki-maintenance, citation
  mermaid, plotting, paper-impl, data-verify, marp
  trace-analyzer, wiki-health

Storage:
  /raw/       → source papers (read-only)
  /wiki/      → LLM-maintained knowledge base
  /skills/    → self-improving skill files
  /memories/  → AGENTS.md schema + preferences
```

---

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended)
- [LangSmith account](https://smith.langchain.com/) (free tier)
- [Daytona account](https://daytona.io/) (free trial, no credit card)
- Anthropic or OpenAI API key

---

## Setup

```bash
# 1. Clone
git clone https://github.com/yourname/paper2wiki
cd paper2wiki

# 2. Create environment
uv venv --python 3.11
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

# 3. Install dependencies
uv add deepagents langchain-anthropic langgraph-checkpoint-postgres
uv add langchain-daytona daytona-sdk  # sandbox
uv add ipykernel jupyter              # notebooks

# 4. Register Jupyter kernel
uv run python -m ipykernel install --user --name=paper2wiki

# 5. Copy and fill env file
cp .env.example .env
```

`.env`:
```bash
ANTHROPIC_API_KEY=sk-ant-...
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_TRACING=true
LANGSMITH_PROJECT=paper2wiki
DAYTONA_API_KEY=...          # from daytona.io dashboard
```

---

## Project Structure

```
paper2wiki/
├── README.md
├── pyproject.toml
├── langgraph.json              ← LangSmith deployment config
├── .env
├── .env.example
├── .gitignore
│
├── memories/
│   └── AGENTS.md               ← pre-created by you
│                               preferences.md created by agent
├── src/
│   ├── supervisor.py
│   ├── ingest.py
│   ├── code.py
│   ├── query.py
│   └── middleware/
│       ├── loop_detection.py
│       ├── precomplete_checklist.py
│       └── local_context.py
│
├── skills/
│   ├── paper-ingestion/
│   │   ├── SKILL.md
│   │   └── fetch_arxiv.py
│   │   └── lint.py
│   │   └── parse_citations.py
│   ├── mermaid/
│   │   ├── SKILL.md
│   │   └── validate.sh
│   ├── trace-analyzer/
│   │   ├── SKILL.md
│   │   └── analyze.py
│   ├── wiki-health/
│   │   └── SKILL.md
│   ├── plotting/SKILL.md
│   └── marp/SKILL.md
│
├── wiki/                       ← gitignored
│   ├── index.md
│   ├── log.md
│   ├── papers/
│   ├── concepts/
│   ├── entities/
│   ├── comparisons/
│   ├── syntheses/
│   ├── graph/
│   ├── outputs/
│   └── health/
│
├── raw/                        ← gitignored
│
├── notebooks/
│   └── explore.ipynb
│
└── tests/
    ├── test_lint.py
    └── test_skill_format.py
```

---
## How The Wiki Works (Karpathy Pattern)

```
raw/        ← YOU put source papers here (immutable)
wiki/       ← AGENT writes and maintains this entirely
AGENTS.md   ← tells agent how to structure and maintain wiki
```

Three operations:
- **Ingest**: new paper → wiki pages + [[wikilinks]] + index.md + log.md
- **Query**: question → read index.md → drill into pages → synthesize → save answer back
- **Lint**: health check → broken links, orphans, contradictions, missing pages

---

## Self-Improvement Loop

```
Inner (every ingest):
  LoopDetectionMiddleware  → prevents wiki update loops
  PreCompletionChecklist   → forces lint + index + log + git commit
  HITL on git commit       → you approve before pushing

Outer (manual):
  trace-analyzer skill     → reads LangSmith traces → rewrites /skills/
  wiki-health skill        → checks entire wiki → health_report.md
  HITL on skill edits      → you approve before skills change
```

---

## Sandbox (Code Subagent)

Uses **Daytona** (thread-scoped, fresh per conversation):

```python
# Code subagent spins up sandbox on demand
# User-requested tasks execute inside isolated container
# Network blocked after paper fetch (XSS protection)
# Rendered outputs (SVG, PNG) served as static files
```

---

## Build Phases

| Phase | What |
|---|---|
| 1 | AGENTS.md + lint.py + minimal agent (FilesystemBackend local) |
| 2 | Skills + middleware (LoopDetection, PreCompletion, LocalContext) |
| 3 | Subagents + StoreBackend + permissions + git HITL |
| 4 | Sandbox (Daytona) + HITL on code tasks |
| 5 | Self-improvement (trace-analyzer + wiki-health skills) |
| 6 | Deploy LangSmith + PostgresStore + qmd search |

---

## License

MIT