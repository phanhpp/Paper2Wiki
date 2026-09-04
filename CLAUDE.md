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
# Most reliable invocation (run from repo root; .env auto-loaded). See src/cli/README.md.
uv run python -m src.cli.app repl                  # interactive chat
uv run python -m src.cli.app chat "ingest <url>"   # one-shot
uv run python -m src.cli.app serve                 # listen on Slack (Loop 3)
uv run python -m src.cli.app fetch git-repo        # phase-1 connector fetch (no LLM)
uv run python -m src.cli.app sessions ls           # browse sessions
uv run python -m src.cli.app config show           # effective config
```

> Flags (on `chat`/`repl`/`sessions resume`): `--thread-id/-t`, `--model/-m` (base model for
> this run — level 3, so `auxiliary.<task>.model` still wins), `--ingest-mode {fast|quality}`,
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
> in `src/cli/README.md`.

Required `.env` vars: `ANTHROPIC_API_KEY`, `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, `LANGSMITH_PROJECT`, `DAYTONA_API_KEY` (Marp). Web ingest needs at least one of `FIRECRAWL_API_KEY`, `TAVILY_API_KEY`, `EXA_API_KEY`. Optional: `WIKI_PATH` (defaults to `./wiki`), `PAPER2WIKI_INGEST_MODE` (`fast` | `quality`). See `.env.example`.

**`.env` has one reader: `src/env.py:load_env()`.** Call it from entry points only —
the CLI callback, eval script `__main__` blocks, a notebook's first cell. Never at module
import time under `src/`. `src/agents/llms.py` used to call `load_dotenv()` at module level,
so importing the agent (which five test files do) set `LANGSMITH_API_KEY` +
`LANGSMITH_TRACING=true` for the whole pytest process and `pytest -m unit` traced fake-model
runs into the live `paper2wiki` project — junk traces that can skew `memories/baselines.json`.
`tests/conftest.py` now forces `LANGSMITH_TRACING=false` at *module* level, not in a fixture:
a fixture runs after collection, and `langsmith.utils.get_env_var` is `lru_cache`d, so a late
env change may never be read.

## Architecture

### Agent system

The supervisor agent is constructed in `src/agents/agent.py:create_supervisor()` and run via `src/agents/stream.py:run_turn_stream()`. The flow:

1. **Supervisor** (`GuardedLocalShellBackend`, `virtual_mode=True`) — handles wiki ingestion, trace analysis, and general tasks. Runs on the local filesystem but cannot escape the repo root.
2. **`marp-slide-creator` subagent** — a Daytona-sandboxed Deep Agent for slide creation. Constructed in `src/agents/daytona_agent.py`. Skills are uploaded to `/home/daytona/skills/` in the sandbox at startup. Sandbox is persisted and restored per `thread_id`.
3. **HITL interrupts** — both agents use `interrupt_on` for `execute`, `write_file`, and `edit_file`. `renderer.handle_interrupts()` prompts the user; typing `yolo` enables session-scoped auto-approve.

**Three things about the supervisor that are easy to get wrong, and all three have bitten:**

**`execute` gets an allowlisted environment, never `inherit_env=True`.** `LocalShellBackend`
defaults to an **empty** env, so shell commands ran with nothing set. `git` survived (`sh`
supplies a fallback `PATH`) but `gh` could not read `~/.config/gh/hosts.yml` and reported
*"You are not logged into any GitHub hosts"* — an auth error for a missing variable.
`backend_wrapper.shell_env()` now passes a fixed list (`HOME`, `PATH`, `SSH_AUTH_SOCK`, …).
**Do not switch to `inherit_env=True`**: `os.environ` holds every provider key, and
`execute` is *not* path-guarded — only HITL stands between it and the filesystem.
`tests/test_no_secrets_in_repo.py` fails if a secret ever reaches that env.

**Interrupts are read from `aget_state()`, not the `values` stream.** They used to be
detected mid-stream, which prompted **twice for one tool call** — `values` re-emits the
whole state each super-step, so the interrupt just resolved reappeared on resume. **Do not
"fix" a repeat by deduplicating on `Interrupt.id`**: it is
`xxh3_128_hexdigest(checkpoint_ns)` — a hash of the *node position*, not the interrupt's unique ID — so if
two genuine approvals at the same node, they will share the exact same Interrupt.id. Ignoring matching IDs would bypass the second interrupt object and automatically approve the action without asking you. Pinned by `tests/test_stream_interrupts.py`. This bug hit Slack too; both
front-ends share `run_turn_stream_async`.

**File tools and `execute` use different path spaces.** `virtual_mode=True` makes
`read_file`/`write_file` show `/`-rooted virtual paths (`/wiki/index.md`); `execute` runs a
real shell already in the repo root, where those do not exist. The agent used to try
`git -C /llm_wiki …`. The rule now lives in the supervisor prompt (`## Paths`) and in
`skills/trace-analysis/SKILL.md`.

**LLMs** (`src/agents/llms.py`): models are built via the `set_up_llms(model, **kwargs)` factory. Known models (`MODEL_CONFIG`) use tuned settings (retries, timeout, max_tokens, effort); any other string (e.g. `openai:gpt-4o`) is passed straight to `init_chat_model` with generic defaults — so the app is **provider-agnostic**. A `HarnessProfile` for sonnet is registered (disables the general-purpose subagent, adds a "read the relevant skill first" system-prompt suffix).

**Model selection is per-task** (`src/llm_roles.py:get_model_spec(role)` → a `ModelSpec`). The user picks **one base model** (`model.default` + that provider's API key); it drives the supervisor, subagents, and every auxiliary task. Each task can override via an `auxiliary.<task>` block carrying `provider`/`model`/`base_url`/`api_key`/`timeout`/`extra_body` (so one task can target a different provider/endpoint/key — e.g. via OpenRouter). Resolution: base = `PAPER2WIKI_MODEL` env > `model.default` (config.yaml) > Claude fallback; a task = `PAPER2WIKI_MODEL_<TASK>` env > `auxiliary.<task>.model` > base, with provider/base_url/api_key from the task block falling back to the `model:` block — but `timeout` and `extra_body` are task-only and never inherit. Tasks: `supervisor`, `subagent`, `title`, `summarize`, `judge`, `web_summarize`. `set_up_llms` accepts a `ModelSpec` (or a bare model string) and strips Anthropic-only knobs (`effort`/`thinking`) for non-Anthropic providers. All auxiliary call sites go through `init_chat_model` — none use the raw `anthropic` SDK anymore; provider keys are read from env by LangChain unless `api_key` is set in config. Router features (fallbacks, credential pools) are intentionally **not** here — they belong to the LiteLLM gateway layer.

> **Caveat:** the `HarnessProfile` is still keyed `anthropic:claude-sonnet-4-6`; if the base model is switched to a non-Anthropic provider it won't apply (the general-purpose subagent stays enabled, skill-first suffix is dropped). Dynamic per-provider registration is a TODO.

### Persistence (two SQLite databases in `.sessions/`)

- **`checkpoints.db`** — LangGraph `AsyncSqliteSaver`; stores graph state for interrupt/resume. Lazily initialized as a module-level singleton in `agent.py`.
- **`sessions.db`** — clean message history + FTS5 full-text search. `--no-save`
  (`persist=False`) skips this DB only — it still calls `aget_state()` on
  `checkpoints.db` each turn, which is how a pending interrupt is detected. Schema defined in `src/sessions/sessions_db_setup.py`. Auto-initialized on import via `_sessions_conn` singleton. Use `session_manager.prune_sessions()` for manual cleanup (auto-pruning is intentionally off).

**Pruning is coupled across both DBs by `thread_id`.** `sessions prune` deletes ended sessions from `sessions.db` *and* evicts their checkpoint state from `checkpoints.db` (via `agent.py:prune_checkpoints` → `adelete_thread`) in one invocation, driven by the ids `prune_sessions` returns. `prune_sessions` stays sync (blocking sqlite3); the async eviction's single `asyncio.run` boundary lives in the CLI `prune` command. Full-thread deletion only — never `aprune(keep_latest)` (DeltaChannel-unsafe). User guide in `src/sessions/README.md`.

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

### Connectors (`src/connectors/`) — phase 1 of ingest

Ingest splits in two: **phase 1** hits a source and dumps raw responses to
`connectors/<name>/raw/` with **no LLM**; **phase 2** is an agent run that reads those
dumps and writes wiki pages. Re-synthesising is then free — nothing is re-fetched.

**Connectors never write files.** They implement one method — `fetch(config, cursor) ->
Iterator[Item]` — and `base.py` owns every write, hash and ledger update. That is what
makes the ledger's guarantees unforgeable rather than merely documented.

The ledger (`manifest.json`) records **one entry per item**, not per run:
`content_hash` (dedupe), `deleted_at` (deletion detection), `cursor` (incremental fetch),
`synthesised_at` (the phase-1/phase-2 boundary — without it synthesis cost grows with the
archive). Written after *every* item, so a crash keeps what completed. Deletion is only
inferred on a full sweep, since a cursor-based run sees a window.

`connectors/` is gitignored — dumps get large and eventually personal.

Design rationale and what was taken from OpenWiki: `src/connectors/README.md`.

### Slack front-end (`src/slack/`) — Loop 3

`paper2wiki serve` runs the same agent, same wiki, driven by Slack messages instead of
the terminal. **Socket Mode** (the app dials out over a websocket), so there's no
webhook, no public URL and no cron — it answers only while `serve` is running, which is
the honest cost of a laptop assistant.

Slack is **optional**: `repl`/`chat`/`sessions`/`config` all work with no Slack tokens.
Only `serve` requires `SLACK_BOT_TOKEN` + `SLACK_APP_TOKEN` + `SLACK_CHANNEL_ID`, checked
by `require_keys(..., slack=True)`. They're workspace-scoped and per-user, so every user
creates their own app — walkthrough in `README.md` → Slack; **the bot must be
`/invite`d to the channel even when it's public**, or it silently receives nothing.

Three design points (detail in `src/slack/README.md`):
- **No new schema.** `sessions.db`'s `id` *is* the LangGraph `thread_id`, so
  `thread_id_for()` derives `slack-{channel}-{thread_ts or message_ts}` — same Slack
  thread → same id → `checkpoints.db` resumes it.
- **One turn at a time.** A single worker task drains an `asyncio.Queue`, awaiting each
  job fully before the next: the two SQLite DBs are single-writer, and Loop 2's snapshot
  diff would otherwise attribute a concurrent run's writes to the wrong run.
- **`Renderer.handle_interrupts` is async** (`src/agents/renderer.py`), and so is
  `build_decisions` — its three callbacks are awaited. The terminal renderers are
  `async def` that never await (stdin blocks); Slack awaits an `asyncio.Future` that
  `submit_decision()` resolves when the button is clicked, so the whole app runs on one
  event loop with no threads. Decision shapes still come from the shared
  `build_decisions()`, so Slack and the terminal can't drift. `edit`/`respond` aren't
  offered in Slack (they need typed args); reject-with-reason works via a modal.

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

**PR gate** (`eval/run_gate.py`, `eval/pr_gate_cases.json`): tool-level checks, no agent/LLM. Two case types:
- `regression` (blocks) — **deterministic, no-network** only (categories: `hashing`, `boundary`/SSRF guards, `health`). Must hold 100%; any drop blocks merge. Only categories listed in `REGRESSION_THRESHOLDS` are gate-checked.
- `capability` (tracked, never blocks) — network/external behavior (web search/extract, arXiv). Promote to `regression` (change `type`) once it's reliably deterministic. There is **no baseline file** — promotion is the lock-in. `run_gate.py` prints a promotion nudge (also surfaced on the PR via `$GITHUB_STEP_SUMMARY`).

Secrets: the blocking core is secret-free; web-provider keys (`FIRECRAWL`/`TAVILY`/`EXA`) are *optional* — mapped into the gate job so web cases run on same-repo PRs, and skip gracefully on forks / when unset.

**Golden evals** (`eval/run_weekly_eval.py`): run the full agent against LangSmith golden datasets (ingest / query / marp) with LLM-as-judge evaluators. Path-conditional — only triggered when relevant files change (avoids burning LLM calls on doc-only PRs).

Evaluator gating: each golden dataset example lists the evaluators it opts into via `metadata["evaluators"]`. The `_gate()` wrapper skips evaluators not listed for a given case and records `score=None` instead of failing.

### Tier 3 — Weekly CI (scheduled)

Weekly job (`ci.yml` `weekly`) does **one** thing:
- `eval/run_weekly_baselines.py` — fetches traces, `compute_baselines_async` merges medians into `memories/baselines.json`, CI commits and pushes to `main`

There is deliberately **no weekly replay**. A `pytest -m langsmith` job used to replay `hard_error` examples from the anomaly datasets; it was removed as redundant, because a hard error becomes durable coverage by being *promoted into* `eval/pr_gate_cases.json`, which then runs on **every** PR — strictly more often. The only signal lost is the `recovery_quality` LLM judge, which a deterministic gate can't express.

Golden agent evals (`eval-ingest` / `eval-query` / `eval-marp`) are path-conditional on PRs; weekly schedule runs all three when enabled.

### Closed feedback loop

`trace-analysis` skill → surfaces hard errors, latency spikes, token blowouts, HITL rejections → auto-generates candidate `eval/pr_gate_cases.json` entries with inferred assertions → HITL approval → fix and regression case land in the same PR, permanently hardening the gate.

**Which half is actually enforced:** `interrupt_on` covers `execute`/`write_file`/`edit_file` (`agent.py:208`), so pushing spans to LangSmith datasets fires **no** prompt — the skill asks by convention. Only appending to `pr_gate_cases.json` is enforced, because it's a `write_file`. The datasets are therefore a superset of what reaches the gate.

### Commands

```bash
# Tier 1 — unit (no secrets)
uv run pytest -m unit

# Tier 2 — PR gate (secret-free core; web cases need a FIRECRAWL/TAVILY/EXA key, else skip)
uv run --env-file .env python eval/run_gate.py

# Tier 2 — golden evals (requires LANGSMITH_API_KEY + ANTHROPIC_API_KEY)
uv run --env-file .env python eval/run_weekly_eval.py --dataset ingest
uv run --env-file .env python eval/run_weekly_eval.py --dataset query --use-cached-transformer-query

# Tier 3 — weekly baselines (no replay step; see Tier 3 above)
uv run --env-file .env python eval/run_weekly_baselines.py
```

**Known CI constraints:**
- `wiki/` **is** committed (16 files) and acts as a test fixture — five test files read it, and the `wiki_check_runs` gate case runs the integrity check against it. Tests still skip gracefully when pages are absent, but the committed wiki is what they normally exercise
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
- **CLI: `/model` — switch model mid-session.** `-m/--model` only applies at launch; changing
  it currently means quitting and restarting. `/new` already rebuilds the agent, so the
  mechanism exists: set `PAPER2WIKI_MODEL`, rebuild the supervisor, keep the same
  `thread_id` so history survives. hermes-agent has this (`hermes_cli/model_switch.py`)
  plus a `model_aliases:` config map for short names and tab-completion — worth copying
  the alias idea only after `/model` itself works.
- CLI: **enable autocompletion** — `sessions resume`/`rename` already pass
  `autocompletion=_complete_session_ref` (`commands/sessions.py:304,346`) and
  `--install-completion` is the documented way to turn completion on, but `app.py`'s
  `add_completion=False` removes that flag, so neither works. Flip it to `True`, or drop
  the completers.
- **Rename `paper2wiki` → `any2wiki`** (49 files). Cosmetic docs first; then the console
  script (`pyproject.toml:32`, `app.py:27`/`:52` — note the package name is a third name,
  `llm-wiki`); then `PAPER2WIKI_*` env vars with a deprecation window; **`LANGSMITH_PROJECT`
  last and separately** — renaming the project splits trace history and leaves
  `detect_anomalies_async` without baselines until weekly CI repopulates them.
- Consolidation agent + cron
- RL
