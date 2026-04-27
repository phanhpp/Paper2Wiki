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

Required `.env` vars: `ANTHROPIC_API_KEY`, `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, `LANGSMITH_PROJECT`, `DAYTONA_API_KEY`
