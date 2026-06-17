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

# CLI (paper2wiki) — interactive REPL, one-shot chat, session browsing
# Most reliable invocation (run from repo root; .env auto-loaded). See docs/cli.md.
uv run python -m src.cli.app repl                  # interactive chat
uv run python -m src.cli.app chat "ingest <url>"   # one-shot
uv run python -m src.cli.app sessions ls           # browse sessions
uv run python -m src.cli.app config show           # effective config
```

> Flags (on `chat`/`repl`/`sessions resume`): `--thread-id/-t`, `--ingest-mode {fast|quality}`,
> `--wiki-path`, `--yes/-y` (auto-approve HITL), `--eval-mode` (skip Daytona),
> `--no-save` (don't write to `sessions.db` — no title, no history; for testing), `--debug`.
> REPL meta-commands: `/title <name>` (name the session), `/new`, `/help`, `/exit` (bare `quit`/`exit`/`bye`/`:q` and Ctrl-D also quit).
> `sessions resume <id|title>` and `sessions rename <id|title> <new>` accept a thread ID or title.
> **macOS caveat (occasional):** the bare `paper2wiki` console command (via `uv run paper2wiki`
> or an activated venv) normally works. Occasionally — usually right after a `uv sync` — it fails
> with `ModuleNotFoundError: No module named 'src'`: uv has flagged the editable `.pth` `UF_HIDDEN`
> and CPython's `site` skips hidden `.pth` files. To fix, run
> `chflags nohidden .venv/lib/python*/site-packages/__editable__.llm_wiki-*.pth`. The `-m` form
> `uv run python -m src.cli.app …` sidesteps the `.pth` entirely and never hits this. Full details
> in `docs/cli.md`.

Required `.env` vars: `ANTHROPIC_API_KEY`, `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, `LANGSMITH_PROJECT`, `DAYTONA_API_KEY` (Marp). Web ingest needs at least one of `FIRECRAWL_API_KEY`, `TAVILY_API_KEY`, `EXA_API_KEY`. Optional: `WIKI_PATH` (defaults to `./wiki`), `PAPER2WIKI_INGEST_MODE` (`fast` | `quality`). See `.env.example`.

## Architecture

### Agent system

The supervisor agent is constructed in `src/agents/agent.py:create_supervisor()` and run via `src/agents/stream.py:run_turn_stream()`. The flow:

1. **Supervisor** (`GuardedLocalShellBackend`, `virtual_mode=True`) — handles wiki ingestion, trace analysis, and general tasks. Runs on the local filesystem but cannot escape the repo root.
2. **`marp-slide-creator` subagent** — a Daytona-sandboxed Deep Agent for slide creation. Constructed in `src/agents/daytona_agent.py`. Skills are uploaded to `/home/daytona/skills/` in the sandbox at startup. Sandbox is persisted and restored per `thread_id`.
3. **HITL interrupts** — both agents use `interrupt_on` for `execute`, `write_file`, and `edit_file`. `stream.py:_handle_interrupts()` prompts the user at the CLI; typing `yolo` enables session-scoped auto-approve.

**LLMs** (`src/agents/llms.py`): models are built via the `set_up_llms(model, **kwargs)` factory, which reads per-model settings (retries, timeout, max_tokens, effort) from `MODEL_CONFIG`. The supervisor runs on `claude-sonnet-4-6` (effort `medium`); the `marp-slide-creator` subagent runs on `claude-haiku-4-5`. A `HarnessProfile` for sonnet is registered (disables the general-purpose subagent, adds a "read the relevant skill first" system-prompt suffix).

### Persistence (two SQLite databases in `.sessions/`)

- **`checkpoints.db`** — LangGraph `AsyncSqliteSaver`; stores graph state for interrupt/resume. Lazily initialized as a module-level singleton in `agent.py`.
- **`sessions.db`** — clean message history + FTS5 full-text search. Schema defined in `src/sessions/sessions_db_setup.py`. Auto-initialized on import via `_sessions_conn` singleton. Use `session_manager.prune_sessions()` for manual cleanup (auto-pruning is intentionally off).

**Pruning is coupled across both DBs by `thread_id`.** `sessions prune` deletes ended sessions from `sessions.db` *and* evicts their checkpoint state from `checkpoints.db` (via `agent.py:prune_checkpoints` → `adelete_thread`) in one invocation, driven by the ids `prune_sessions` returns. `prune_sessions` stays sync (blocking sqlite3); the async eviction's single `asyncio.run` boundary lives in the CLI `prune` command. Full-thread deletion only — never `aprune(keep_latest)` (DeltaChannel-unsafe). User guide in `src/sessions/README.md`; design rationale in `docs/pruning_design.md`.

### Tools (`src/tools/`)

All tools exposed to the supervisor are aggregated in `src/tools/__init__.py:all_tools`, built by `_build_tools()`. Adding a new tool: implement it under `src/tools/` and add it to the `_build_tools()` return list.

**Ingest mode** (`src/ingest_mode.py:get_ingest_mode()`, resolved as env `PAPER2WIKI_INGEST_MODE` > config file > default `fast`) gates which ingest tools are registered:
- `fast` (default) — web tools only (`web_search`, `web_extract`).
- `quality` — also registers `fetch_arxiv` and `parse_pdf_docling`.

| Tool | Mode | Purpose |
|---|---|---|
| `web_search` | all | Web search over Firecrawl / Tavily / Exa (provider routed via config) |
| `web_extract` | all | Extract/scrape page content from a URL |
| `fetch_arxiv` | quality | Download papers from arXiv by ID |
| `parse_pdf_docling` | quality | Parse PDFs to markdown via Docling |
| `compute_sha256` | all | Compute the `raw/` body sha256 (see Wiki structure) |
| `quick_wiki_integrity_check` | all | Validate wiki frontmatter, links, index/log consistency |
| `run_trace_report_async` | all | Fetch LangSmith traces (pass `error=True` for error-only runs) |
| `summarize_traces_async` | all | Summarize fetched traces for self-improvement |
| `detect_anomalies_async` | all | Flag `hard_error` / `latency_spike` / `token_blowout` / `step_count_spike` vs baselines |
| `compute_baselines_async` | all | Refresh rolling latency/token/step medians from recent traces |
| `create_datasets_from_anomaly_report` | all | Push failing spans to LangSmith datasets + generate candidate PR-gate cases |

### Wiki structure (`wiki/`)

Schema and conventions are in `wiki/SCHEMA.md`. Key rules:
- Every page needs YAML frontmatter; all tags must be in the taxonomy defined in `SCHEMA.md`.
- `raw/` sha256 is computed as `hashlib.sha256(body.lstrip('\n').encode('utf-8')).hexdigest()` where `body` is everything after the closing `---` delimiter.
- Every new page must be added to `wiki/index.md`; every action appended to `wiki/log.md`.

### Agent memory (`memories/`)

- `memories/AGENTS.md` — project/environment knowledge (tool quirks, architecture notes, task diary).
- `memories/USER.md` — user profile (communication preferences, skill level).
- `memories/baselines.json` — rolling per-run-name latency/token/step medians for `detect_anomalies_async` (3× spike thresholds). **Tracked in git**; weekly CI refreshes it from live LangSmith traces.

**Anomaly baselines sync** — before manual trace analysis, `git pull` so local `detect_anomalies_async` uses CI-fresh thresholds:

```text
Weekly CI
  → fetch traces (run_weekly_baselines.py)
  → update memories/baselines.json
  → commit + push to main

You (manual trace analysis)
  → git pull
  → detect_anomalies_async reads memories/baselines.json
```

No need to run `eval/run_weekly_baselines.py` locally unless you want an ad-hoc refresh between CI runs.

### Skills (`skills/`)

Skills available to the supervisor:
- `skills/llm-wiki/` — wiki building conventions
- `skills/trace-analysis/` — LangSmith trace analysis workflow
- `skills/web-tools/` — web search/extract provider selection and call patterns (Firecrawl, Tavily, Exa)
- `skills/marp-slide/` — Marp slide creation (injected into the Daytona sandbox)

### CLI front-end (`src/cli/`)

Terminal REPL/one-shot front-end. The agent stream emits events through the `Renderer`
protocol (`src/agents/renderer.py`); `RichRenderer` (`src/cli/renderer.py`) is the Rich +
prompt_toolkit terminal implementation, `DefaultRenderer` is the plain-`print` one used by
notebooks/tests. The loop lives in `src/cli/commands/chat.py`.

**Per-turn UI lifecycle** (one turn): `on_turn_start` shows a transient "Thinking…" spinner →
first `on_token` swaps it for a live Markdown block → `on_turn_end` commits the block. Tool
results route to `on_tool_result` (collapsed preview, full text stashed), **not** to the
Markdown stream. HITL interrupts route to `handle_interrupts`.

**Verified by unit tests** (`pytest -m unit`; `tests/test_cli.py`, `tests/test_stream_persist.py`):
- HITL decisions: approve / reject (with & without reason) / edit (JSON + Python-dict + invalid
  fallback) / **respond** / yolo / auto-approve short-circuit; `choices_for` filters to the
  tool's `allowed_decisions`. Decision shapes match `langchain ... human_in_the_loop`.
- `RichRenderer.on_tool_result` previews + stashes full output; `open_last_tool_output` pages it
  (and the empty case); `on_turn_start` clears the per-turn store. Both renderers satisfy the
  protocol (incl. `on_tool_result`).
- `stream.py` routes a `ToolMessage` to `on_tool_result`, assistant text to `on_token`.
- `persist=False` (`--no-save`) skips `sessions.db`; `persist=True` saves.

**Verified manually only** (real run under a pty; no automated coverage yet):
- Spinner → Markdown handoff (spinner clears on first token).
- Long-output Markdown no longer smears/repeats — `on_token` streams into a `transient=True`,
  `vertical_overflow="crop"` Live, and `_end_live` commits the full Markdown once. (The earlier
  `vertical_overflow="visible"` reprinted the whole block each refresh on overflow.)

**Known gaps / to fix:**
- **HITL `respond` vs `reject`:** "tell the agent to do it differently" is **reject + a reason
  message** (feedback, tool not run), *not* `respond` (which returns the text as a successful
  tool result, for ask-user tools). Both are now offered when allowed.
- **Ctrl-O only works at the `you ❯` prompt, not mid-stream** — prompt_toolkit reads keys only
  while awaiting prompt input. `/open` (alias `/last`) is the always-available equivalent.
- **Ctrl-O / `/open` can't toggle closed** — they launch a real pager (`less`); close with `q`.
  A Claude-Code-style open/close toggle while tokens keep streaming needs an inline, managed-
  render expand (not a pager) — i.e. the Textual rewrite below.
- **Interactive bits lack automated tests** (spinner, smear, Ctrl-O) — verified only by hand.
  Future: `pexpect`/`pyte`-based tests, or Textual `Pilot` if migrated.


**Future — Textual TUI (Option A):** to match Claude Code (mid-stream Ctrl-O expand/collapse of
a *scrollable* tool output while streaming continues) the REPL must be a continuously-rendered
TUI that owns input + rendering. Scope it as a `TextualRenderer` behind the existing `Renderer`
protocol, gated by a flag (e.g. `--tui`), keeping the Rich path as default. The agent/stream/
persistence layers are already decoupled and stay unchanged. Orthogonal to the LiteLLM work
(UI layer vs model-provider layer).

## Testing Strategy

Three tiers — each catches a different class of failure.

### Tier 1 — Unit tests (every PR, no secrets, ~10s)

`pytest -m unit` — mock all I/O, never touch network or real files. Test your logic: cache hit/miss, ID extraction, error paths, evaluator functions, path guards, schema validation. Fast and deterministic.

### Tier 2 — PR gate + path-conditional golden evals (every PR, LangSmith secrets optional)

**PR gate** (`eval/run_gate.py`, `eval/pr_gate_cases.json`): deterministic tool-level checks with no LLM calls. Two case types:
- `regression` — must hold 100%; any drop blocks merge
- `capability` — tracked but not blocking; promoted to regression once stable

**Golden evals** (`eval/run_weekly_eval.py`): run the full agent against LangSmith golden datasets (ingest / query / marp) with LLM-as-judge evaluators. Path-conditional — only triggered when relevant files change (avoids burning LLM calls on doc-only PRs).

Evaluator gating: each golden dataset example lists the evaluators it opts into via `metadata["evaluators"]`. The `_gate()` wrapper skips evaluators not listed for a given case and records `score=None` instead of failing.

### Tier 3 — Weekly CI (scheduled)

Weekly job (`ci.yml` `weekly`):
- `eval/run_weekly_baselines.py` — fetches traces, `compute_baselines_async` merges medians into `memories/baselines.json`, CI commits and pushes to `main`
- `pytest -m langsmith` — replays `hard_error` examples from HITL-reviewed LangSmith datasets; gates on no regressions (does not read `baselines.json`)

Golden agent evals (`eval-ingest` / `eval-query` / `eval-marp`) are path-conditional on PRs; weekly schedule runs all three when enabled.

### Closed feedback loop

`trace-analysis` skill → surfaces hard errors, latency spikes, token blowouts, HITL rejections → auto-generates candidate `eval/pr_gate_cases.json` entries with inferred assertions → HITL approval → fix and regression case land in the same PR, permanently hardening the gate.

### Commands

```bash
# Tier 1 — unit (no secrets)
uv run pytest -m unit

# Tier 2 — PR gate (no secrets)
uv run python eval/run_gate.py

# Tier 2 — golden evals (requires LANGSMITH_API_KEY + ANTHROPIC_API_KEY)
uv run --env-file .env python eval/run_weekly_eval.py --dataset ingest
uv run --env-file .env python eval/run_weekly_eval.py --dataset query --use-cached-transformer-query

# Tier 3 — weekly baselines + langsmith regression
uv run --env-file .env python eval/run_weekly_baselines.py
uv run --env-file .env pytest -m "langsmith and not slow and not integration" -q
```

**Known CI constraints:**
- `wiki/` is not committed — wiki-dependent tests skip gracefully when pages are absent
- `test_fetch_arxiv_downloads_paper` hits real arXiv network — marked `integration`, excluded from CI to avoid 429s

**When adding a new tool**, add:
1. A unit test with mocked I/O covering the main logic branches
2. A golden dataset example in the appropriate `eval/golden_datasets/*.json`

## Pending Cleanup

- `src/tools/trace_report_pickle_cache.py` is dev-only. It uses pickle to replay LangSmith runs locally and should not be used in production. The production trace path is `src/tools/observability_eval_tools/fetch_traces.py` (`run_trace_report_async`).

## Todos

- Capacity limit for /memories/
- ~~Wrap agent into CLI~~ — done (`src/cli/`, run via `python -m src.cli.app`)
- CLI: mid-stream Ctrl-O (capture keys during a turn) + open/close toggle — see "CLI front-end"
  (lightweight `loop.add_reader` in cbreak mode, or the full Textual TUI)
- CLI: automated tests for interactive paths (spinner / smear / Ctrl-O) via `pexpect`/`pyte`
- Consolidation agent + cron
- RL
