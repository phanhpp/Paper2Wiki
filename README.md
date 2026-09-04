# Paper2Wiki: A Self-Improving Research Assistant

A research assistant that transforms research papers into durable artifacts — wiki pages, slide decks, and code. Powered by the [Deep Agents SDK](https://github.com/langchain-ai/deepagents), it can refine its own skills and logic on-demand by analyzing its execution traces.

---

## What It Does

- **LLM-Wiki**: builds and maintains a graph-structured knowledge base from academic papers
- **Marp slides**: generates presentation decks from papers or wiki content (sandboxed via Daytona)
- **Self-improvement**: fetches its own LangSmith traces, detects metric anomalies (tool crashes, token blowouts, latency/step spikes) and qualitative patterns (skill deviations, HITL rejections, tool misuse), then patches its own **skill prompts** and `AGENTS.md` — HITL approval required before any change is committed
- **Model-agnostic**: bring any LLM provider (Anthropic, OpenAI, Google, OpenRouter, local Ollama…) via `config.yaml` — set one base model for everything, or a different model per task (supervisor, subagents, titling, summaries, eval judge) with its own provider/endpoint/key
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

- **Supervisor**: Handles high-level orchestration, wiki maintenance, and trace analysis. Runs on your local machine with guarded shell access. Its model — and each subagent/auxiliary task's — is provider-agnostic, resolved from `config.yaml` (see [Choosing your LLM](#choosing-your-llm)).
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
- An API key for your chosen LLM provider — Anthropic, OpenAI, Google, etc. (see [Choosing your LLM](#choosing-your-llm))

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
ANTHROPIC_API_KEY=sk-ant-...  # or OPENAI_API_KEY / GOOGLE_API_KEY — match your chosen model
LANGSMITH_API_KEY=lsv2_...
LANGSMITH_TRACING=true        # Required for self-improvement
LANGSMITH_PROJECT=paper2wiki
DAYTONA_API_KEY=...           # Required for Marp slides
WIKI_PATH=./wiki              # Optional: custom wiki location
PAPER2WIKI_MODEL=             # Optional: base LLM for all roles, e.g. openai:gpt-4o (default: claude-sonnet-4-6)
```

### Choosing your LLM

> **Full guide: [`MODELS.md`](MODELS.md)** — copy-paste recipes for Anthropic, OpenAI,
> Gemini, OpenRouter, Ollama (local and cloud) and the LiteLLM gateway, plus troubleshooting.
> The essentials are below.

**Pick one model, and everything uses it.** Set `model.default` in `config.yaml` and that
provider's API key — it drives the supervisor, the subagents, and every background task.
Any LangChain model string works, including the `provider:model` form:
`openai:gpt-4o`, `google_genai:gemini-2.0-flash`, `anthropic:claude-sonnet-4-6`.

```yaml
# config.yaml
model:
  default: openai:gpt-4o
```

That's the whole setup. The rest of this section is for when you want one task to differ.

#### Which model a task ends up using

Five levels. **The first one that exists wins:**

```
Task-Specific Env Var  →  Task Config  →  Global Env Var  →  Base Config  →  Default Fallback
```

| Level | Where | Example | Applies to |
|---|---|---|---|
| **1 · Task-Specific Env Var** | `.env` | `PAPER2WIKI_MODEL_SUMMARIZE=openai:gpt-4o-mini` | one task |
| **2 · Task Config** | `config.yaml` | `auxiliary.summarize.model: openai:gpt-4o-mini` | one task |
| **3 · Global Env Var** | `.env` | `PAPER2WIKI_MODEL=openai:gpt-4o` | every task |
| **4 · Base Config** | `config.yaml` | `model.default: openai:gpt-4o` | every task |
| **5 · Default Fallback** | built in | `claude-sonnet-4-6` | every task |

**`--model` / `-m` is level 3.** The flag writes `PAPER2WIKI_MODEL` for that one run, so it
beats `model.default` but *not* a task's own `auxiliary.<task>.model`. Run
`paper2wiki config show -m <model>` to see exactly which tasks it moved.

The pattern behind the order: **task beats global, and env beats config.**

So `PAPER2WIKI_MODEL_SUMMARIZE` (1) wins over `auxiliary.summarize.model` (2), which wins
over `PAPER2WIKI_MODEL` (3), which wins over `model.default` (4). And if you set nothing at
all, you get `claude-sonnet-4-6` (5).

In practice: levels 4 and 3 are how you pick your model, and levels 2 and 1 are how you
make one task differ — permanently in the file, or temporarily with an env var.

The tasks you can name: `supervisor`, `subagent`, `title`, `summarize`, `judge`,
`web_summarize`.

#### Giving one task a different provider

An `auxiliary.<task>` block can carry more than a model name, so a single task can go to a
different provider, a different endpoint, or a different key:

```yaml
auxiliary:
  summarize:
    model: openai:gpt-4o-mini
    base_url: https://openrouter.ai/api/v1   # any OpenAI-compatible gateway
    api_key: sk-or-...                       # its own key
    timeout: 60
```

`provider`, `base_url` and `api_key` fall back to the `model:` block if the task doesn't
set them. **`timeout` and `extra_body` do not** — they apply only to the task that sets
them. See `config.example.yaml`.

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
chat, a Slack listener, connector fetches, session browsing, and config inspection. Run
commands **from the repo root** — `.env` is auto-loaded.

### Running it

The most reliable invocation (works regardless of your PATH or editable-install state):

```bash
uv run python -m src.cli.app repl                                  # interactive chat
uv run python -m src.cli.app chat "ingest https://arxiv.org/abs/…" # one-shot, then exit
uv run python -m src.cli.app serve                                 # listen on Slack (Loop 3)
uv run python -m src.cli.app fetch git-repo                        # connector fetch, no LLM
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
| `serve` | Listen on a Slack channel and run the agent on each message ([below](#slack-serve)) | yes |
| `fetch [connector]` | Phase 1 of ingest — pull raw source data to `connectors/<name>/`. Omit the name to run every configured connector ([below](#connector-fetch-fetch)) | **no** |
| `sessions ls [-n N]` | List recent sessions, newest first | no |
| `sessions stats` | Catalog summary + how many you'd prune at each age threshold | no |
| `sessions search "<query>"` | Full-text search message history | no |
| `sessions resume <id\|title>` | Reopen a past session in the REPL | yes |
| `sessions rename <id\|title> "<new>"` | Rename a session | no |
| `sessions prune [--older-than-days N] [-y]` | Delete old ended sessions (+ their checkpoints) | no |
| `sessions prune-orphans [--apply] [--vacuum] [--older-than D] [--full]` | Evict checkpoints with no session row (dry run by default) | no |
| `config show` | Print the effective config (ingest mode, wiki path, providers) | no |

`sessions`/`config`/`fetch` don't load the agent stack, so they're fast; `repl`/`chat`/`serve`
build the supervisor (and, unless `--eval-mode`, a Daytona sandbox).

### Common flags

| Flag | Purpose | Available on |
|---|---|---|
| `--model` / `-m` | Override the **base** model for this run, e.g. `openai:gpt-4o` | `chat` `repl` `resume` `serve` `config show` |
| `--ingest-mode {fast\|quality}` | Override ingest mode | `chat` `repl` `resume` `serve` `fetch` |
| `--wiki-path` | Override the wiki directory | `chat` `repl` `resume` `serve` `fetch` |
| `--debug` | Show diagnostic output (incl. startup timings) | `chat` `repl` `resume` `serve` `fetch` |
| `--yes` / `-y` | Auto-approve all approval prompts | `chat` `repl` `resume` `serve` |
| `--eval-mode` | Skip the Daytona sandbox (no Marp subagent) | `chat` `repl` `resume` `serve` |
| `--thread-id` / `-t` | Resume / pin a specific thread | `chat` `repl` `resume` |
| `--no-save` | Don't persist to `sessions.db` (throwaway turns; in-run approvals still work) | `chat` `repl` `resume` |
| `--channel` | Slack channel id (else `$SLACK_CHANNEL_ID`) | `serve` |

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

### Slack (`serve`)

`serve` is the **same agent against the same wiki**, driven by Slack messages instead of the
terminal (Loop 3). Same `create_supervisor()`, same `wiki/`, same `checkpoints.db` and
`sessions.db` — only the front-end differs.

```bash
paper2wiki serve                          # listen on $SLACK_CHANNEL_ID
paper2wiki serve --channel C0123456789    # override the channel
paper2wiki serve --eval-mode              # no Daytona sandbox (no Marp subagent)
paper2wiki serve -y                       # auto-approve — no approval buttons at all
```

**Setup is per-user.** Slack apps are workspace-scoped, so everyone creates their own —
everyone creates their own. Three env vars, checked at startup:

```bash
SLACK_BOT_TOKEN=xoxb-...   # Bot User OAuth Token   (OAuth & Permissions page)
SLACK_APP_TOKEN=xapp-...   # App-Level Token        (Basic Information page — different page)
SLACK_CHANNEL_ID=C...      # the channel to listen on
```

> **The bot must be `/invite`d to the channel — even a public one.** Without it, `serve`
> starts cleanly, connects, and silently receives nothing. This is the usual thing to get
> wrong.

**How it behaves:**

- **Socket Mode** — the app dials *out* over a websocket, so there's no webhook, no public
  URL, no ngrok. The cost is honest: it answers only while `serve` is running.
- **A Slack thread is a session.** `thread_id_for()` derives `slack-{channel}-{thread_ts}`,
  which *is* the `sessions.db` id and the LangGraph `thread_id` — so replying in a thread
  resumes that conversation from `checkpoints.db`, exactly like `sessions resume`.
- **Approvals are Block Kit buttons.** Approve / reject appear in-thread; reject-with-reason
  opens a modal. `edit` and `respond` aren't offered (they need typed arguments) — use the
  terminal for those.
- **One turn at a time.** A single worker drains a queue, finishing each job before starting
  the next: both SQLite DBs are single-writer, and Loop 2's snapshot diff would otherwise
  blame one run for another's writes.
- **Ctrl-C to stop.** It exits cleanly rather than dumping a traceback.

Slack is **optional** — `repl`, `chat`, `fetch`, `sessions` and `config` all work with no
Slack tokens set. Message flow and design: [`src/slack/README.md`](src/slack/README.md).

### Connector fetch (`fetch`)

Phase 1 of ingest, and the only command that never calls an LLM. It hits a source, writes
the raw responses under `connectors/<name>/raw/`, and records every item in a manifest —
then stops. Turning that into wiki pages is a separate agent run, which can be repeated for
free because the raw data is already on disk.

```bash
paper2wiki fetch              # run every connector enabled in config.yaml
paper2wiki fetch git-repo     # run one
```

Each run reports `N new · N unchanged · N gone`. Unchanged items are skipped by content
hash, so a second run costs almost nothing. Connectors carry their own credentials (or
none — `git-repo` needs nothing), so `fetch` doesn't require `ANTHROPIC_API_KEY` or
`DAYTONA_API_KEY`. Enable one under `connectors:` in `config.yaml`; see
[`src/connectors/README.md`](src/connectors/README.md).

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

Five steps. Two of them stop and wait for you.

1. **Fetch** — `run_trace_report_async` pulls recent traces (`error=True` scopes to failures).
2. **Summarize** — `summarize_traces_async` condenses them in parallel.
3. **Cluster & detect** — group findings by pattern, then run `detect_anomalies_async`.
   > **Waits for you** to acknowledge the ranked report.
4. **Push to datasets** — failing spans go to LangSmith; tool hard errors also become draft
   PR-gate cases with inferred assertions.
   > **Waits for you** to approve before writing `eval/pr_gate_cases.json`.
5. **Commit & PR** — skill / `AGENTS.md` patches plus the new gate cases, one PR, and a
   watermark in `trace_analysis_log.md`.

Two details worth knowing:

- **Step 3 checks git history first** (`git log -n 20 -- <file>`) and strikes any finding a
  recent commit already fixed — traces are always older than the code.
- **Step 4 proposes the assertion; you decide it.** The tool hands back only the failing
  inputs — no assertion at all. The skill reads the error message and suggests one:

  | Error signal | Reading | Suggests |
  |---|---|---|
  | `No paper found for ID INVALID999` | the input was junk — it *should* fail | `expect_error: true` |
  | `KeyError: 'pdf_path'` on `1706.03762` | a real paper, so this is a bug; once fixed it should return data | `expect_keys: ["title", "pdf_path", "metadata"]` |

  You approve or correct it. Full field schema in
  [`eval/README.md`](eval/README.md#pr-gate-case-schema).

The fix and its gate case land in the **same PR** — and that case then runs on every PR
thereafter, which is what makes the fix durable.

### Two kinds of finding

Step 3 produces two streams, and conflating them is the usual confusion:

- **Qualitative** — from clustering the summaries: `skill_deviation`, `hitl_rejected`, tool
  misuse. The model's *reading* of what went wrong → patches a `SKILL.md` or `AGENTS.md`.
- **Quantitative** — from `detect_anomalies_async`, measured against `baselines.json`:
  `hard_error`, `latency_spike`, `token_blowout`, `step_count_spike` → a LangSmith dataset
  entry, and for **tool hard errors only**, a PR-gate case.

**A qualitative finding changes a prompt; a quantitative one can become a test.** Only a
hard error on a tool run is reproducible enough to block a merge — spikes aren't, and an
llm-run error has no tool to re-call. Detail in [`eval/README.md`](eval/README.md).

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

## Loop Engineering

Paper2Wiki is built as a worked example of [loop engineering](https://www.langchain.com/blog/the-art-of-loop-engineering) — stacking feedback and execution loops *around* the model instead of relying on the model alone. Each loop catches what the tighter loop inside it cannot.

| Loop | Goal | How Paper2Wiki implements it |
|---|---|---|
| **1 · Agent** | automate the work | Supervisor + Daytona marp subagent (Deep Agents SDK); skill-driven tools for ingest, query, slides, and trace analysis. HITL on every `write_file` / `edit_file` / `execute`. |
| **2 · Verification** | correctness | `WikiRubricMiddleware` (`src/middleware/`) classifies each run as ingest / query / marp from a **filesystem diff** — catching writes made through the shell, which tool-call scanning misses — then runs 16 deterministic checks (frontmatter, wikilink resolution, `index.md` + `log.md`, graph nodes/edges, source hashes). On failure it sends the agent back with the specific gaps, capped at `max_iterations`, then surfaces the verdict. **No LLM, so the loop is free.** |
| **3 · Event-driven** | run without being asked | `paper2wiki serve` — a Slack front-end over Socket Mode (outbound websocket, so no public URL or webhook). A message starts a turn, a threaded reply resumes it, approvals are Block Kit buttons. Same agent, same wiki as the terminal — it reuses the `Renderer` protocol, so the agent and persistence layers are untouched. |
| **4 · Hill-climbing** | improve the harness | Weekly CI refreshes anomaly baselines from live traces; the `trace-analysis` skill turns detected failures into versioned LangSmith datasets and candidate PR-gate cases, HITL-approved. A production bug lands its regression test in the same PR as its fix — and that case then runs on every PR thereafter. |

**Human oversight is a primitive at every level**, not an escape hatch: tool approvals in Loops 1 and 3, the retry cap surfacing to the user in Loop 2, and mandatory approval before any harness change is committed in Loop 4.

**Not built yet:** Loop 3's *scheduled* half (a cron/heartbeat trigger — today it only reacts to messages), Loop 2's two semantic query checks (they need an embedding index), and Loop 4's judge calibration (LLM judges score quality but don't yet gate merges).

---

## Developer Information

### Project Structure

```text
llm_wiki/
├── src/agents/       # Supervisor & Daytona subagent logic (Loop 1)
├── src/middleware/   # WikiRubricMiddleware — in-run verification (Loop 2)
├── src/slack/        # Socket Mode front-end (Loop 3)
├── src/connectors/   # Phase 1 of ingest — deterministic fetch, no LLM
├── src/tools/        # Ingest (Docling, arXiv), Trace, & Wiki tools
├── src/cli/          # Terminal REPL / chat / serve / fetch / sessions
├── skills/           # Skill definitions (Markdown + logic)
├── wiki/             # The knowledge base
└── marp-slides/      # Presentation outputs
```

`src/middleware/`, `src/slack/`, `src/cli/` and `src/connectors/` each carry their own
`README.md` — the checks, the Slack message flow, the terminal front-end, and the fetch
contract respectively.

### Tests & CI

A three-track eval system turns production failures into regression coverage. Unit tests still run as a fast preflight, but the core CI design is the three-track harness below.

```text
Preflight — Every PR
  pytest -m unit            Mocked I/O, deterministic logic, path guards

Track 1 — PR Gate (every PR, no agent, no LLM; secret-free core)
  eval/pr_gate_cases.json   Tool inputs + assertions
                            regression (blocks): deterministic, no-network — hashing / boundary
                                                 (SSRF guards) / health. Must score 100%.
                            capability (tracked): network/external (web search/extract, arXiv).
                                                 Never blocks; promote to regression once stable.
  eval/run_gate.py          Invoke tool calling directly, writes eval/results.json
                            Web-provider keys optional: web cases run when a key is set, else skip.

Track 2 — Golden Agent Eval (weekly, or path-conditional on PR)
  golden_datasets/*.json    Versioned ingest/query/marp cases
  push_golden_datasets.py   Syncs cases to LangSmith datasets
  run_weekly_eval.py        Runs the agent end-to-end with appropriate-scoped HITL auto-approval
  golden_evaluators.py      Code checks + LLM judges over LangSmith experiment results

Track 3 — Anomaly Replay Loop (weekly + HITL only)
  run_weekly_baselines.py   Fetches traces and refreshes latency/token/step medians
  trace-analysis skill      HITL-approved anomaly detection + dataset creation
  (promotion, not replay)   An approved hard_error becomes a pr_gate_cases.json entry,
                            which then runs on every PR — that is what locks the fix in
```

**Track 1: deterministic PR gate** — fast regression checks that exercise tools directly with versioned inputs and assertions, before any LLM or agent runtime. The **blocking** cases are deterministic and key-free (hash-convention correctness, SSRF/boundary guards, wiki integrity), so the 100% floor never flakes. Network/external behavior (web search/extract, arXiv) is tracked as non-blocking **capability** — it uses a web-provider key when one is set (mapped in CI), and skips gracefully otherwise.

**Track 2: golden agent evals** — end-to-end agent runs over curated ingest, query, and slide-generation scenarios. Code checks validate trajectories and required artifacts, while LLM judges score groundedness, faithfulness, and task-specific quality.

**Track 3: anomaly loop** — weekly CI fetches recent LangSmith traces to refresh the anomaly baselines. The `trace-analysis` skill (HITL) turns detected failures into LangSmith datasets and, for tool hard errors, candidate PR-gate cases. **Promotion is what makes a fix durable**: once a case is in `pr_gate_cases.json` it runs on every PR, so there is no separate weekly replay to maintain.

**Closed feedback loop** — the trace-analysis skill surfaces failures across the full stack (tool hard errors, latency spikes, token blowouts, step-count anomalies, HITL rejections). For hard errors it auto-generates candidate eval/pr_gate_cases.json entries with inferred assertions and waits for HITL approval before committing. The fix and its regression case land in the same PR, permanently hardening the gate against that failure recurring.


### Optional: LiteLLM gateway (multi-tenant proxy)

`gateway/` holds an **optional**, self-contained LiteLLM proxy for learning/operating multi-tenant
LLM access — virtual keys + per-team budgets/RBAC, spend alerts, Prometheus metrics, fallbacks,
a prompt-injection guardrail, and a semantic response cache (Postgres + Redis). It's **not part of
the published package** (lives outside `src/`); the app only talks to it over HTTP when
`PAPER2WIKI_LLM_GATEWAY=litellm` is set, and never imports `litellm`. Setup, rationale, and the
"lie to LangChain" routing trick are in [`gateway/README.md`](gateway/README.md).

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
