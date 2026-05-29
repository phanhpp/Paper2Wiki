# Paper2Wiki: A Self-Improving Research Assistant

A research assistant that transforms research papers into durable artifacts — wiki pages, slide decks, and code. Powered by the [Deep Agents SDK](https://github.com/langchain-ai/deepagents), it can refine its own skills and logic on-demand by analyzing its execution traces.

---

## What It Does

- **LLM-Wiki**: builds and maintains a graph-structured knowledge base from academic papers
- **Marp slides**: generates presentation decks from papers or wiki content (sandboxed via Daytona)
- **Self-improvement**: analyzes LangSmith traces to surface failures, then proposes fixes to **skills** and `AGENTS.md`
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

The agent uses the `trace-analysis` skill to self-improve by analyzing its own performance:

1. **Fetch**: Retrieves recent execution traces from LangSmith (filtered by success or failure).
2. **Summarize**: Uses an LLM to condense traces into a structured report of what happened.
3. **Analyze**: Identifies patterns, anomalies, skill deviations, or tool misuse.
4. **Validate**: Checks git history to ensure findings haven't already been fixed.
5. **Propose**: Suggests actionable improvements to `/skills/` or `AGENTS.md`.
6. **Apply**: Implements approved changes after human confirmation.

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

### Tests

CI runs two jobs on every PR and push to `main`:

| Job | Markers | Needs secrets | What it checks |
|---|---|---|---|
| **Unit** | `not integration, not slow, not langsmith` | No | Pure logic, mocked I/O |
| **Regression** | `langsmith and not slow` | `LANGSMITH_API_KEY`, `ANTHROPIC_API_KEY` | Live tools against real LangSmith datasets |

```bash
# fast — unit only (no secrets needed)
uv run pytest -m "not integration and not slow and not langsmith" -q

# regression gate (requires .env)
LANGSMITH_TEST_SUITE="paper2wiki-regression" \
LANGSMITH_TEST_CACHE=tests/cassettes \
uv run pytest -m "langsmith and not slow" -q

# slow tests — opt-in locally (runs Docling, ~2 min)
uv run pytest -m slow -q
```

#### Regression test suite (`tests/test_regression.py`, `tests/test_anomaly_regression.py`)

**`test_wiki_health_check_runs_clean`** — runs `quick_wiki_integrity_check` against the real `wiki/` dir. Hard gate: `wiki-check: OK`. Soft: `error_count` logged to LangSmith.

**`test_existing_wiki_pages_quality`** — parametrized over 3 known-good pages. Hard gates via `expect()`: each page has at least one `[[wikilink]]`, valid frontmatter, and >200 chars. Soft: `wikilink_count`, `header_count` tracked as trends.

**`test_pdf_parse_produces_content`** *(slow, opt-in)* — fetches arXiv `1706.03762` and runs Docling. Hard gates: output >500 chars, contains `##` headers, parse finishes in <3 min (`expect.value(elapsed).to_be_less_than(180)`). Does **not** check for wikilinks — those are written by the agent, not the parser.

**`test_anomaly_regression`** — dynamically parametrized over every LangSmith dataset matching `__rt_` in the name (new format) or `"failures"` in the description (legacy). For each example: replays the failing tool call, logs `local_latency_s` as soft metric. Hard gate for `hard_error` examples: `assert not outputs.get("error")`. Also runs an LLM judge (`claude-haiku-4-5`) via `t.trace_feedback()` to score recovery quality when the tool doesn't error.

#### Metrics logged to LangSmith per test

| Test | Soft (`t.log_feedback`) | Hard (`assert` / `expect`) |
|---|---|---|
| wiki health | `error_count` | `result == "wiki-check: OK"` |
| wiki page quality | `wikilink_count`, `header_count` | `expect(content).to_contain("[[")`, `expect.value(len) > 200` |
| pdf parse | `content_length`, `has_headers` | `len > 500`, `"##" in content`, `elapsed < 180s` |
| anomaly regression | `local_latency_s`, `recovery_quality` (LLM judge) | `not outputs.get("error")` for hard-error examples |

Fixture-backed trace tests use `tests/fixtures/runs.json` when present and skip when missing. Generate locally with:

```bash
uv run --env-file .env python -m tests.save_fixtures
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
