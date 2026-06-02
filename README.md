# Paper2Wiki: A Self-Improving Research Assistant

A research assistant that transforms research papers into durable artifacts — wiki pages, slide decks, and code. Powered by the [Deep Agents SDK](https://github.com/langchain-ai/deepagents), it can refine its own skills and logic on-demand by analyzing its execution traces.

---

## What It Does

- **LLM-Wiki**: builds and maintains a graph-structured knowledge base from academic papers
- **Marp slides**: generates presentation decks from papers or wiki content (sandboxed via Daytona)
- **Self-improvement**: fetches its own LangSmith traces, detects metric anomalies (tool crashes, token blowouts, latency/step spikes) and qualitative patterns (skill deviations, HITL rejections, tool misuse), then patches its own **skill prompts** and `AGENTS.md` — HITL approval required before any change is committed
- **General assistance**: answers questions, writes/edits code, and runs repo tools (with HITL where configured)

---

## Architecture

Paper2Wiki uses a supervisor-subagent architecture powered by the [Deep Agents SDK](https://github.com/langchain-ai/deepagents).

```text
Supervisor Agent (Local)
├── Tools: Ingest, Query, Trace Fetcher
├── Skills: llm-wiki, trace-analysis
└── Subagent: marp-slide-creator (Daytona Sandbox)
    └── Skill: marp-slide
```

- **Supervisor**: Handles high-level orchestration, wiki maintenance, and trace analysis. Runs on your local machine with guarded shell access.
- **Marp Subagent**: An isolated Daytona container dedicated to generating and styling presentation decks.
- **HITL (Human-in-the-Loop)**: By default, the system interrupts and asks for approval before any `write_file`, `edit_file`, or `execute` (shell/git) operation.


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
git clone https://github.com/phanhpp/paper2wiki
cd paper2wiki

# 2. Create venv and install deps from pyproject.toml + uv.lock
uv sync

# 3. (Optional) Jupyter kernel for notebooks
uv run python -m ipykernel install --user --name=paper2wiki

# 4. Copy and fill env file
cp .env.example .env
```

`.env`:

```bash
ANTHROPIC_API_KEY=sk-ant-...
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_TRACING=true        # Required for self-improvement
LANGSMITH_PROJECT=paper2wiki
DAYTONA_API_KEY=...           # Required for Marp slides
WIKI_PATH=./wiki              # Optional: custom wiki location
```

## Usage

You can interact with Paper2Wiki using natural language. Here are common patterns:

### 1. Ingesting Papers
- "Ingest this paper: https://arxiv.org/abs/2312.00752"
- "Add the 'Attention is All You Need' paper to my wiki"
- "Search for recent papers on 'LoRA' and ingest the best one"

### 2. Querying the Wiki
- "What are the main findings of the ReAct paper?"
- "Compare the architectural differences between Llama 3 and Mistral"
- "Summarize everything we know about 'Chain of Thought' prompting"

### 3. Generating Presentations
- "Create a 5-slide deck about the 'Attention' paper using the 'tech' theme"
- "Make a presentation summarizing our wiki's content on 'Model Merging'"
- "Restyle the existing slides in `marp-slides/intro.md` to look more professional"

### 4. Self-Improvement
- "Analyze my recent traces and improve your skills"
- "Why did the last ingest fail? Check the traces and fix the issue"

---

## Storage Structure

- `/wiki/`: The core knowledge base (Markdown + JSON)
- `/skills/`: Self-improving skill definitions (SKILL.md files)
- `/memories/`: Long-term agent guidance (`AGENTS.md`)
- `/marp-slides/`: Final presentation outputs (PDF, PNG, Markdown)

---

## How The Wiki Works (Karpathy Pattern)

The wiki subsystem is inspired by the [Karpathy LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

```text
raw/        ← YOU put source papers here (immutable)
wiki/       ← AGENT writes and maintains this entirely (compiled artifacts)
```

Concretely, on ingest the agent extracts and maintains:

- **Navigation + provenance**: `wiki/index.md` (catalog + 1-line summaries), `wiki/log.md` (append-only actions), `wiki/SCHEMA.md` (structure/tag taxonomy)
- **Immutable sources + parsed artifacts**: `wiki/raw/` plus `wiki/raw/assets/<slug>/` with
  - `images/` (figures), `tables/` (extracted tables), and `<slug>.md` (markdown parse of the PDF)
- **Compiled pages**: entity/concept/comparison/query pages under `wiki/entities/`, `wiki/concepts/`, `wiki/comparisons/`, `wiki/queries/` with YAML frontmatter + `[[wikilinks]]`
- **Graphs (optional but supported)**: `wiki/graph/graph.json` (nodes/edges, confidence) and `wiki/graph/citations.json` (citation metadata + reference links)

Three operations:

- **Ingest**: new paper → wiki pages + [[wikilinks]] + index.md + log.md
- **Query**: question → read index.md → drill into pages → synthesize → save answer back
- **Lint**: health check → broken links, orphans, contradictions, missing pages

---

## How Trace Analysis Works

Trigger with: *"Analyze my recent traces"* or *"What went wrong in the last few runs?"*

1. **Fetch** — `run_trace_report_async` retrieves recent traces from LangSmith (pass `error=True` to scope to failures only).
2. **Summarize** — `summarize_traces_async` batches traces into pages and condenses them in parallel into structured summaries.
3. **Cluster & detect** — the agent groups findings by pattern (skill deviations, tool errors, HITL rejections), validates each against git history to skip already-fixed issues, then runs `detect_anomalies_async` to produce ground-truth anomaly signals (`hard_error`, `latency_spike`, `token_blowout`, `step_count_spike`). Presents a ranked report and **waits for your confirmation** before proceeding.
4. **Push to datasets** — `create_datasets_from_anomaly_report` pushes failing spans to scoped LangSmith datasets (used by the weekly CI regression suite). For tool hard errors, it also generates candidate `eval/cases.json` entries, presents them with inferred assertions (`expect_error` or `expect_keys`), and **waits for your approval** before writing. Approved cases are added to `eval/cases.json` in the same commit — so the fix PR also hardens the PR gate against that failure recurring.
5. **Commit & PR** — commits all changes (skill patches, `AGENTS.md` updates, `eval/cases.json` additions), opens a PR, and appends a watermark to `trace_analysis_log.md`.

---

## Marp Slide Creation (Isolated Sandbox)

The agent uses a dedicated subagent in a Daytona sandbox to create presentations:

1. **Isolation**: Runs in a fresh, thread-scoped container for every conversation.
2. **Workflow**:
   - **Selects** a professional theme (Business, Tech, Minimal, etc.).
   - **Structures** content from papers or wiki pages into Marp Markdown.
   - **Exports** the deck to multiple formats (SVG, PNG, PDF).
3. **Security**: Network access is restricted to prevent data exfiltration.
4. **Persistence**: Final outputs are saved to the host's `marp-slides/` folder.

---

## Developer Information

### Project Structure

```text
llm_wiki/
├── src/agents/       # Supervisor & Daytona subagent logic
├── src/tools/        # Ingest (Docling, arXiv), Trace, & Wiki tools
├── skills/           # Skill definitions (Markdown + logic)
├── wiki/             # The knowledge base
└── marp-slides/      # Presentation outputs
```

### Tests & CI

CI uses a two-layer eval strategy with a closed feedback loop from production:

```text
Every PR (no secrets, ~30s)
  Unit tests       — mocked I/O, deterministic logic
  Eval gate        — calls tools directly, asserts on real outputs

Weekly (LangSmith secrets)
  run_weekly.py    — fetch traces → update baselines only
  pytest-langsmith — replay hard_error examples from HITL-reviewed datasets, gate on no regressions
```

**PR gate (`eval/run_gate.py`)** — deterministic tool-level checks versioned in `eval/cases.json`. Two case types:

- `regression` — must hold 100%; any drop blocks merge (SSRF protection, arXiv ID lookup, wiki integrity)
- `capability` — tracked but not gate-blocking; promoted to regression once stable

**Weekly pipeline (`eval/run_weekly.py`)** — refreshes baselines against the last 7 days of production traces:

1. `compute_baselines_async` — updates rolling per-run-name latency/token/step medians in `memories/baselines.json`
2. `pytest -m langsmith` — replays `hard_error` examples from LangSmith datasets (populated via HITL); gates on no regressions

Anomaly detection and dataset writes are HITL-only (via `trace-analysis` skill) — pushing automatically risks committing infra noise as regression examples.

**Closed loop:** running the `trace-analysis` skill surfaces failures across the full stack — tool and LLM hard errors, latency spikes, token blowouts, step-count anomalies, skill deviations, and HITL rejections. For hard errors, it auto-generates candidate `eval/cases.json` entries with inferred assertions and asks for HITL approval before committing. The fix and its regression case land in the same PR, permanently hardening the gate against that failure recurring.

```bash
# PR gate (no secrets needed)
uv run python eval/run_gate.py

# Unit tests
uv run pytest -m "not integration and not slow and not langsmith" -q

# Weekly pipeline (requires LANGSMITH_API_KEY + ANTHROPIC_API_KEY)
uv run --env-file .env python eval/run_weekly.py
LANGSMITH_TEST_SUITE="paper2wiki-regression" \
LANGSMITH_TEST_CACHE=tests/cassettes \
uv run pytest -m "langsmith and not slow and not integration" -q
```

### Wiki Integrity (Linting)

The system includes a `wiki-health` skill that performs both programmatic and LLM-based checks:

- **Programmatic**: Broken links, orphan pages, index completeness, frontmatter validation.
- **LLM-based**: Semantic contradictions, "missing concept" detection, and quality assessment.

Trigger it with: *"Run a wiki health check"*

---

## License

MIT

## References

- **Marp-slide skill**: Adapted from [softaworks/agent-toolkit](https://github.com/softaworks/agent-toolkit).
- **LLM-wiki skill**: Adapted from [hermes-agent](https://github.com/NousResearch/hermes-agent).
- **Wiki Pattern**: Inspired by [Andre Karpathy's LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).
