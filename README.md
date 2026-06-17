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

## Context Management (Automatic Compaction)

Long ingests and trace analyses can generate huge tool outputs and long histories. Paper2Wiki
relies on the **Deep Agents SDK's built-in context management** — we add **no** summarization
middleware, so the framework defaults do the work automatically. The agent doesn't "know" it
happened; its working memory just stays clean.

| Mechanism | When it fires | What happens |
|---|---|---|
| **Offloading** | a single tool result exceeds **~20,000 tokens** | the full result is written to the filesystem; the agent keeps only a short **preview + file path** and can re-read on demand |
| **Summarization** | the context window reaches **~85%** capacity | older history is summarized in place and the run resumes; the full record is preserved (it's never silently lost) |

This is **automatic** in the Deep Agents SDK (in raw LangGraph you'd hand-write an offload node,
a `summarize_node` with conditional edges, and `trim_messages` yourself).

What we *do* configure in `src/agents/agent.py` are complementary **guardrails**, not compaction:

- `ModelCallLimitMiddleware(run_limit=20)` — caps model calls per run (sized for worst-case ingest).
- `ToolCallLimitMiddleware("web_extract", run_limit=2, thread_limit=4)` — caps expensive scrapes.
- `PIIMiddleware` — redacts/masks emails, credit cards, and API keys from input.

We do **not** add the optional `compact_conversation` tool (agent-triggered early compaction) —
the stateless defaults above are sufficient for current workloads.

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

## Using the CLI

Paper2Wiki ships a terminal CLI (`paper2wiki`) for daily use: an interactive REPL, one-shot
chat, session browsing, and config inspection. Run commands **from the repo root** — `.env`
is auto-loaded.

### Running it

The most reliable invocation (works regardless of your PATH or editable-install state):

```bash
uv run python -m src.cli.app repl                                  # interactive chat
uv run python -m src.cli.app chat "ingest https://arxiv.org/abs/…" # one-shot, then exit
uv run python -m src.cli.app sessions ls                           # browse past sessions
uv run python -m src.cli.app config show                           # show effective config
```

Optional alias so it reads like a real command:

```bash
echo "alias paper2wiki='uv run python -m src.cli.app'" >> ~/.zshrc && source ~/.zshrc
paper2wiki repl
```

(A `paper2wiki` console script is also installed in the venv; it needs `.venv/bin` on your
PATH — either `source .venv/bin/activate` or use `uv run paper2wiki …`.)

### Commands

| Command | What it does | Needs LLM? |
|---|---|---|
| `repl` | Interactive chat session (streaming, approvals, meta-commands) | yes |
| `chat "<msg>"` | Run a single message and exit | yes |
| `sessions ls [-n N]` | List recent sessions, newest first | no |
| `sessions stats` | Catalog summary + how many you'd prune at each age threshold | no |
| `sessions search "<query>"` | Full-text search message history | no |
| `sessions resume <id\|title>` | Reopen a past session in the REPL | yes |
| `sessions rename <id\|title> "<new>"` | Rename a session | no |
| `sessions prune [--older-than-days N] [-y]` | Delete old ended sessions (+ their checkpoints) | no |
| `sessions prune-orphans [--apply] [--vacuum] [--older-than D] [--full]` | Evict checkpoints with no session row (dry run by default) | no |
| `config show` | Print the effective config (ingest mode, wiki path, providers) | no |

`sessions`/`config` don't load the agent stack, so they're fast; `repl`/`chat` build the
supervisor (and, unless `--eval-mode`, a Daytona sandbox).

### Common flags (on `chat` / `repl` / `sessions resume`)

| Flag | Purpose |
|---|---|
| `--thread-id` / `-t` | Resume / pin a specific thread |
| `--ingest-mode {fast\|quality}` | Override ingest mode |
| `--wiki-path` | Override the wiki directory |
| `--yes` / `-y` | Auto-approve all approval prompts |
| `--eval-mode` | Skip the Daytona sandbox (no Marp subagent) |
| `--no-save` | Don't persist to `sessions.db` (throwaway turns; in-run approvals still work) |
| `--debug` | Show diagnostic output |

### Inside the REPL

- Chat in natural language (see [Usage](#usage) above).
- **Approvals (HITL):** tool calls pause for review. Choose **a** approve · **e** edit args ·
  **r** reject *(with an optional reason sent back to the agent so it tries differently)* ·
  **s** respond *(answer on the tool's behalf, for ask-user tools)* · **yolo** approve all for
  the session. Only the options a given tool allows are shown.
- **Long tool output** is shown as a short preview; **`/open`** (or **Ctrl-O** at the prompt)
  pages the full output — press **`q`** to close. *(Ctrl-O works at the `you ❯` prompt, not
  mid-stream.)*
- **Meta-commands:** `/title <name>` · `/new` · `/help` · `/open` (alias `/last`) · `/exit`
  (bare `quit`/`exit`/`bye`/`:q` and Ctrl-D also quit).

### Pruning old sessions

History is kept indefinitely by default (it powers `sessions search`). When you want to clean
up, run `sessions stats` first to see the totals, time range, and how many sessions you'd delete
at each age threshold — then prune. **Pruning is manual and explicit** — one command tidies both
stores in lockstep:

```bash
paper2wiki sessions stats                      # see totals + what each threshold would delete
paper2wiki sessions prune                      # delete ended sessions older than 90 days
paper2wiki sessions prune --older-than-days 30 # custom age threshold
paper2wiki sessions prune -y                   # skip the confirmation prompt
```

- **Preview before delete:** `prune` lists the date + title of every session it will remove and
  asks to confirm, so you can judge each by its title (answer `n` to inspect without deleting).
- **What's removed:** ended sessions past the threshold — their chat history (`sessions.db`)
  **and** their resumable graph state (`checkpoints.db`), keyed by the same `thread_id`.
- **What's kept:** active sessions are never pruned, regardless of age.
- **Irreversible:** a pruned session can no longer be searched *or* resumed.

`prune` is the only `sessions` subcommand that loads the checkpointer; `ls` / `search` /
`resume` stay fast.

#### Orphan checkpoints

`prune` only evicts checkpoints whose **session row still exists** — it's driven by deleted
sessions. Runs that never wrote a session row (`--no-save` turns, eval/test threads, history
predating `sessions.db`) leave **orphan** checkpoints that `prune` can't reach, and they're
usually the bulk of `checkpoints.db`. Sweep them separately:

```bash
paper2wiki sessions prune-orphans                      # dry run — lists orphans + each one's last-activity age
paper2wiki sessions prune-orphans --full               # list every orphan (not just the first 20)
paper2wiki sessions prune-orphans --older-than 1       # skip threads active in the last day
paper2wiki sessions prune-orphans --apply              # actually evict them (via adelete_thread)
paper2wiki sessions prune-orphans --apply --vacuum     # also shrink the file on disk
```

- **Dry run by default** — review the list (each orphan shows its last-activity age), then re-run
  with `--apply`. Use `--full` to print the whole list instead of the first 20.
- **`--older-than DAYS`** skips recently-active threads (last activity read from each thread's
  most recent checkpoint) — use it to avoid evicting a session mid-first-turn whose session row
  hasn't been written yet. Note: **larger values are *more* restrictive** (fewer matches); if it
  matches nothing it tells you how many orphans exist and their age range.
- **Safety guard:** if `sessions.db` is empty it refuses (everything would look orphaned).
- **`--vacuum`** reclaims disk: plain deletes only free pages internally, so the file doesn't
  shrink until you VACUUM (a one-shot rebuild). Without it, orphans are gone but the file stays
  the same size.

### macOS note

Occasionally (usually right after `uv sync`) the bare `paper2wiki` command fails with
`ModuleNotFoundError: No module named 'src'` — a uv editable-`.pth` hidden-flag quirk. Fix:
`chflags nohidden .venv/lib/python*/site-packages/__editable__.llm_wiki-*.pth`. The
`uv run python -m src.cli.app …` form sidesteps it entirely.

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
4. **Push to datasets** — `create_datasets_from_anomaly_report` pushes failing spans to scoped LangSmith datasets (used by the weekly CI regression suite). For tool hard errors, it also generates candidate `eval/pr_gate_cases.json` entries, presents them with inferred assertions (`expect_error` or `expect_keys`), and **waits for your approval** before writing. Approved cases are added to `eval/pr_gate_cases.json` in the same commit — so the fix PR also hardens the PR gate against that failure recurring.
5. **Commit & PR** — commits all changes (skill patches, `AGENTS.md` updates, `eval/pr_gate_cases.json` additions), opens a PR, and appends a watermark to `trace_analysis_log.md`.

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

A three-track eval system turns production failures into regression coverage. Unit tests still run as a fast preflight, but the core CI design is the three-track harness below.

```text
Preflight — Every PR
  pytest -m unit            Mocked I/O, deterministic logic, path guards

Track 1 — PR Gate (every PR, no agent, no LLM, no secrets)
  eval/pr_gate_cases.json   Tool inputs + assertions
  eval/run_gate.py          Invoke tool calling directly, writes eval/results.json
  regression_baseline.json  Score floor per category; regression drops block merge

Track 2 — Golden Agent Eval (weekly, or path-conditional on PR)
  golden_datasets/*.json    Versioned ingest/query/marp cases
  push_golden_datasets.py   Syncs cases to LangSmith datasets
  run_weekly_eval.py        Runs the agent end-to-end with appropriate-scoped HITL auto-approval
  golden_evaluators.py      Code checks + LLM judges over LangSmith experiment results

Track 3 — Anomaly Replay Loop (weekly + HITL only)
  run_weekly_baselines.py   Fetches traces and refreshes latency/token/step medians
  trace-analysis skill      HITL-approved anomaly detection + dataset creation
  test_anomaly_regression.py Replays hard_error examples; fixed bugs must stay fixed
```

**Track 1: deterministic PR gate** — fast, secret-free regression checks that exercise tools directly with versioned inputs and assertions. This catches boundary bugs, retrieval failures, and schema regressions before any LLM or agent runtime is involved.

**Track 2: golden agent evals** — end-to-end agent runs over curated ingest, query, and slide-generation scenarios. Code checks validate trajectories and required artifacts, while LLM judges score groundedness, faithfulness, and task-specific quality.

**Track 3: anomaly replay loop** — weekly CI fetches recent LangSmith traces to update baselines, then replays HITL-approved `hard_error` examples from LangSmith datasets to confirm previously fixed failures do not recur.

**Closed feedback loop** — the trace-analysis skill surfaces failures across the full stack (tool hard errors, latency spikes, token blowouts, step-count anomalies, HITL rejections). For hard errors it auto-generates candidate eval/pr_gate_cases.json entries with inferred assertions and waits for HITL approval before committing. The fix and its regression case land in the same PR, permanently hardening the gate against that failure recurring.


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
