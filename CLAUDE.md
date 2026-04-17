# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Paper2Wiki** — a self-improving LLM knowledge base implementing the [Karpathy LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f). Users ingest research papers and an agent builds/maintains a structured, interlinked wiki that compounds knowledge over time. Built on the Deep Agents SDK (LangGraph-based).

## Setup & Commands

```bash
# Environment (Python 3.11 required)
uv venv --python 3.11
source .venv/bin/activate

# Install dependencies
uv add deepagents langchain-anthropic langgraph-checkpoint-postgres
uv add langchain-daytona daytona-sdk
uv add ipykernel jupyter

# Register Jupyter kernel
uv run python -m ipykernel install --user --name=paper2wiki

# Run tests
uv run pytest

# Wiki health check (once scripts/lint.py exists)
python scripts/lint.py --wiki-dir wiki/
```

Required `.env` vars: `ANTHROPIC_API_KEY`, `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, `LANGSMITH_PROJECT`, `DAYTONA_API_KEY`.

## Architecture

### Agent Hierarchy
```
Supervisor Agent (src/supervisor.py)
├── Ingest Subagent  (src/ingest.py)   → PDF / arXiv URL / DOI / web article / topic → wiki pages
├── Code Subagent    (src/code.py)     → coding tasks in Daytona sandbox + HITL
└── Query Subagent   (src/query.py)    → questions → web + wiki → answers → filed back to wiki
```

### Middleware (src/middleware/)
- `loop_detection.py` — prevents wiki update loops during ingest
- `precomplete_checklist.py` — enforces lint + index.md + log.md updates + git commit before agent completes
- `local_context.py` — injects local filesystem context

### Skills (skills/<name>/SKILL.md)

| Subagent | Skills |
|---|---|
| Ingest | `paper-ingestion` — all input types, wiki page creation, wikilinks, index, log, lint, git, image handling, citation graph |
| Code | `mermaid`, `plotting`, `marp` |
| Supervisor (manual) | `trace-analyzer`, `wiki-health` |

The `trace-analyzer` skill reads LangSmith failure traces and rewrites other skill files (self-improvement). All skill edits require HITL approval.

### Storage Layout
```
raw/                ← source papers (IMMUTABLE — never modify)
wiki/               ← agent-maintained knowledge base
  index.md              ← catalog of all pages (updated every ingest)
  log.md                ← append-only chronological record
  overview.md           ← evolving synthesis
  papers/               ← one .md per paper
  concepts/             ← cross-paper concept pages
  entities/             ← authors, institutions
  comparisons/          ← synthesized comparisons
  syntheses/            ← query answers filed back as pages
  graph/                ← citation graph edges (JSON)
  outputs/              ← generated artifacts
  health/               ← lint reports
skills/             ← self-improving skill files
memories/           ← AGENTS.md (wiki schema) + preferences.md
workspace/          ← ephemeral scratch (StateBackend)
sandbox/            ← Daytona, thread-scoped (Code subagent only)
```

## Wiki Page Conventions

Every wiki page needs YAML frontmatter:
```yaml
---
type: paper | concept | entity | comparison | synthesis
title: Human readable title
created: YYYY-MM-DD
updated: YYYY-MM-DD
source_count: N
confidence: high | medium | low
tags: [tag1, tag2]
---
```

Wikilinks use `[[slug]]` format (slug = filename without `.md`, lowercase, underscores). Every page must have outbound wikilinks — a page without them is incomplete.

## HITL Gates

```
Ingest:   git_commit_and_push         → approve / reject
Code:     write_file                  → approve / edit / reject
          edit_file                   → approve / edit / reject
          execute                     → approve / edit / reject
Improve:  edit_file  on skills/       → approve / reject
          write_file on skills/       → approve / reject
```

## Key Invariants

- `raw/` is read-only — never write to it
- After every ingest: run lint, update `wiki/index.md`, append to `wiki/log.md`
- All HITL gates must be respected — see table above
- Valuable query answers get filed back into `wiki/comparisons/` or `wiki/syntheses/`
- Contradictions between pages → flag in health report, never auto-resolve
- `AGENTS.md` defines the authoritative wiki schema and is loaded at agent startup
