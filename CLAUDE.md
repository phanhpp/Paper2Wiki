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
uv sync

# Register Jupyter kernel
uv run python -m ipykernel install --user --name=paper2wiki

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_arxiv_tool.py

# Run tests by marker (unit, integration, smoke, slow)
uv run pytest -m unit
uv run pytest -m "not integration"  # skip tests needing external services

# Wiki health check (once scripts/lint.py exists)
python scripts/lint.py --wiki-dir wiki/
```

Required `.env` vars: `ANTHROPIC_API_KEY`, `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, `LANGSMITH_PROJECT`, `DAYTONA_API_KEY`

## Architecture

### Agent system

The supervisor agent is constructed in `src/agents/agent.py:create_supervisor()` and run via `src/agents/stream.py:run_turn_stream()`. The flow:

1. **Supervisor** (`GuardedLocalShellBackend`, `virtual_mode=True`) — handles wiki ingestion, trace analysis, and general tasks. Runs on the local filesystem but cannot escape the repo root.
2. **`marp-slide-creator` subagent** — a Daytona-sandboxed Deep Agent for slide creation. Constructed in `src/agents/daytona_agent.py`. Skills are uploaded to `/home/daytona/skills/` in the sandbox at startup. Sandbox is persisted and restored per `thread_id`.
3. **HITL interrupts** — both agents use `interrupt_on` for `execute`, `write_file`, and `edit_file`. `stream.py:_handle_interrupts()` prompts the user at the CLI; typing `yolo` enables session-scoped auto-approve.

**LLMs** (`src/agents/llms.py`): `haiku_llm` (claude-haiku-4-5) is the default for both agents. `expensive_llm` (claude-sonnet-4-6 with adaptive thinking) is available for heavier tasks.

### Persistence (two SQLite databases in `.sessions/`)

- **`checkpoints.db`** — LangGraph `AsyncSqliteSaver`; stores graph state for interrupt/resume. Lazily initialized as a module-level singleton in `agent.py`.
- **`sessions.db`** — clean message history + FTS5 full-text search. Schema defined in `src/sessions/sessions_db_setup.py`. Auto-initialized on import via `_sessions_conn` singleton. Use `session_manager.prune_sessions()` for manual cleanup (auto-pruning is intentionally off).

### Tools (`src/tools/`)

All tools exposed to the supervisor are aggregated in `src/tools/ingest_tools.py:all_tools`. Adding a new tool: implement it there and add it to the list.

| Tool | Purpose |
|---|---|
| `fetch_arxiv` | Download papers from arXiv by ID |
| `parse_pdf_docling` | Parse PDFs to markdown via Docling |
| `quick_wiki_integrity_check` | Validate wiki frontmatter, links, index/log consistency |
| `run_trace_report_async` | Fetch LangSmith traces (pass `error=True` for error-only runs) |
| `summarize_traces_async` | Summarize fetched traces for self-improvement |

### Wiki structure (`wiki/`)

Schema and conventions are in `wiki/SCHEMA.md`. Key rules:
- Every page needs YAML frontmatter; all tags must be in the taxonomy defined in `SCHEMA.md`.
- `raw/` sha256 is computed as `hashlib.sha256(body.lstrip('\n').encode('utf-8')).hexdigest()` where `body` is everything after the closing `---` delimiter.
- Every new page must be added to `wiki/index.md`; every action appended to `wiki/log.md`.

### Agent memory (`memories/`)

- `memories/AGENTS.md` — project/environment knowledge (tool quirks, architecture notes, task diary).
- `memories/USER.md` — user profile (communication preferences, skill level).

### Skills (`skills/`)

Three skills are available to the supervisor:
- `skills/llm-wiki/` — wiki building conventions
- `skills/trace-analysis/` — LangSmith trace analysis workflow
- `skills/marp-slide/` — Marp slide creation (injected into Daytona sandbox)

## Testing Strategy

Two layers — they catch different bugs and are both necessary:

**Unit tests** (`tests/test_arxiv_tool.py`, `tests/test_wiki_integrity_check.py`, etc.) — mock all I/O, never touch the network or real files. Fast and deterministic. Catch regressions in **your logic**: cache hit/miss decisions, ID extraction, error paths, schema validation. Use `@pytest.mark.unit`. Run in CI without secrets.

**Regression/integration tests** (`tests/test_regression.py`, `tests/test_anomaly_regression.py`) — hit real external services or real wiki files. Catch a different class of failure: **the external world drifting away from your assumptions** — arXiv API changes, LangSmith dataset schema changes, wiki pages going stale, Docling output format changes. Unit tests can't catch these because mocks assume the behavior you already observed.

Rule: unit tests are not useless just because they mock — they test your logic. Integration tests are not redundant just because unit tests pass — they test the contract with the outside world.

**CI jobs:**
- `unit` job: `not integration and not slow and not langsmith` — no secrets, always runs
- `regression` job: `langsmith and not slow` — needs `LANGSMITH_API_KEY` + `ANTHROPIC_API_KEY` + `LANGSMITH_TRACING=true`, runs after unit passes
- `slow` / `integration`: opt-in locally only

**Required env vars for regression job** (all must be set or tests fail at runtime):
- `LANGSMITH_API_KEY` — authenticates LangSmith API calls; missing = 401 at collection time
- `LANGSMITH_TRACING=true` — enables `t.log_inputs/outputs/feedback`; missing = `ValueError` at test runtime
- `ANTHROPIC_API_KEY` — needed by the LLM judge in `test_anomaly_regression`
- `LANGSMITH_TEST_CACHE` — path to cassette dir for caching LLM calls; missing `vcrpy` package = `ImportError`

**Known CI constraints:**
- `wiki/` is not committed — `test_existing_wiki_pages_quality` skips gracefully when pages are absent (passes in CI, runs fully locally)
- `test_fetch_arxiv_downloads_paper` hits the real arXiv network — marked `integration`, excluded from regression job to avoid 429 rate limits

**When adding a new tool**, add both:
1. A unit test with mocked I/O covering the main logic branches
2. A `@pytest.mark.integration` or `@pytest.mark.langsmith` test that calls the real thing at least once

## Pending Cleanup

- `src/tools/trace_report_pickle_cache.py` is dev-only. It uses pickle to replay LangSmith runs locally and should not be used in production. `trace_report.py` is the production path.

## Todos

- Integrate anomaly_detection and create_eval_dataset to Trace_analyzer skill
- Capacity limit for /memories/
- Wrap agent into Cli
- Consolidation agent + cron
- RL

## Next Steps — CI Restructure (blocked on web extraction tool)

**Step 1 (current):** Build web extraction tool to replace `fetch_arxiv` + `parse_pdf_docling`.
Input: URL. Output: parsed markdown. No PDF download, no arXiv API dependency.

**Step 2:** Once web extraction lands, restructure CI:

```
Every PR — unit tests only (pytest -m unit):
    lint logic, evaluator fns, is_failure, tool logic
    ~10s, no secrets, no network

Nightly — real agent (GitHub Actions schedule):
    fetch traces (last 24h)
    compute_baselines_async        ← update rolling baseline from fresh traffic
    detect_anomalies_async         ← compare current traces against baseline
    run_evaluate (per dataset)     ← regression check on known failure datasets
    full URL ingest (1 paper, web extraction)
    query flow (read-only, 1 LLM call)
    marp creation (1 LLM call)
```

The current regression job (langsmith and not slow and not integration) runs anomaly
regression on every PR — move it to nightly. The PR gate should be unit tests only.

The meaningful part of this setup is not the PR gate but the closed loop:
    production traces → trace-analyzer → anomaly datasets → nightly regression
    → catch regressions → HITL + skill patch → nightly confirms fix holds

The PR gate protects logic regressions. The nightly loop catches agent failures.
