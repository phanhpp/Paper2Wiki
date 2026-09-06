# The `any2wiki` CLI

Every command, flag and workflow. For choosing a model see `[MODELS.md](MODELS.md)`; for
how the package is built see `[src/cli/README.md](src/cli/README.md)`.

Any2Wiki ships a terminal CLI (`any2wiki`) for daily use: an interactive REPL, one-shot
chat, a Slack listener, connector fetches, session browsing, and config inspection. Run
commands **from the repo root** — `.env` is auto-loaded.

## First run

Three commands. The first two are one-time.

```bash
any2wiki setup          # choose a model, store your API key
any2wiki config show    # check what a run would use — costs nothing
any2wiki repl           # start chatting
```

If `setup` says your config already exists, you are already set up — skip to
`config show`.

## `setup` — the wizard

It asks three questions:

| | question | default |
|---|---|---|
| 1 | Which provider? | `anthropic` |
| 2 | Which model? | `claude-sonnet-4-6` |
| 3 | Use a cheaper model for background tasks? | yes |

Each default is shown in `[brackets]`. **Press Enter three times and you are done** — you
get sonnet for the main agent and haiku for everything else.

```
$ any2wiki setup

Providers (✓ = key already set)
  ✓ anthropic
    google_genai
    openai

Provider [anthropic]:
Suggestions: claude-sonnet-4-6, claude-haiku-4-5-20251001
Base model [claude-sonnet-4-6]:
Use claude-haiku-4-5-20251001 for the 5 background tasks? [Y/n]:

Wrote /Users/you/dev/any2wiki/config.yaml
ANTHROPIC_API_KEY is not set. Enter it now? [Y/n]: y
ANTHROPIC_API_KEY: ••••••••
```

To choose something else, type it instead of pressing Enter — `Provider [anthropic]:
openai`.

That run writes:

```yaml
model:
  default: claude-sonnet-4-6        # the main agent — the one that thinks

auxiliary:                          # everything else, on a cheaper model
  subagent:      {model: claude-haiku-4-5-20251001}
  title:         {model: claude-haiku-4-5-20251001}
  summarize:     {model: claude-haiku-4-5-20251001}
  judge:         {model: claude-haiku-4-5-20251001}
  web_summarize: {model: claude-haiku-4-5-20251001}
```

**The third question is the one that saves money.** Here is what those five tasks are, and
why none of them needs a frontier model:

| task | what it does | runs |
|---|---|---|
| `title` | names a session from your first message, e.g. *"Ingest the ReAct paper"* | once per session |
| `summarize` | condenses LangSmith traces before analysis | trace analysis only |
| `judge` | scores eval runs pass/fail against a rubric | evals only |
| `web_summarize` | shortens a scraped web page before the agent reads it | per web fetch |
| `subagent` | the Marp slide subagent — turns content into a deck | only when making slides |

Your **main agent** — planning, calling tools, writing wiki pages — is the `supervisor`
task, and it always keeps the model you chose in question 2.

Say **no** and all five stay on sonnet. That works fine; it just costs more for naming
sessions.

The API key goes to **`.env`**, never to `config.yaml` — see
[the split below](#where-settings-live-configyaml-vs-env).

**Two behaviours worth knowing:**

- **It will not overwrite an existing `config.yaml`.** You get a message and a non-zero
exit. `--force` overwrites, and *replaces the whole file* — including any per-task pins
you had. To change one setting, use `config set` instead.
- **A missing API key is a warning, not a failure.** Config now, key later is a normal
flow, and a real run refuses with a message naming the exact variable.

Scripted, for a fresh machine or for testing:

```bash
any2wiki setup --provider openai --model openai:gpt-4o --yes
```

### After setup

```bash
any2wiki config show                    # every task's model, provider and endpoint
any2wiki config path                    # which file that came from
any2wiki keys list                      # which keys are set (masked)

any2wiki config set model.default google_genai:gemini-3.5-flash-lite   # change one thing
any2wiki keys set GOOGLE_API_KEY                                  # add a key
```

**If a change seems to do nothing**, read `config show`'s **From** column. An env var
outranks `config.yaml`, and a footer names any that are overriding.

### Trying something without committing to it

Two escape hatches, and they do different jobs:

```bash
# preview a whole config — your real one is untouched
ANY2WIKI_CONFIG=/tmp/try.yaml any2wiki config show

# sandbox the wizard itself — writes config + .env into a throwaway directory
ANY2WIKI_HOME=$(mktemp -d) any2wiki setup --provider google_genai --yes
```

Use the first to test a provider recipe, the second to test `setup` without risking your
own config.

## Where settings live: `config.yaml` vs `.env`

Two files, one rule: `.env` **holds anything secret;** `config.yaml` **holds everything else.**
`config set` refuses a secret-shaped key and `keys set` refuses to write `config.yaml`, so
the split is enforced rather than remembered.


|                | `config.yaml`                                              | `.env`                                      |
| -------------- | ---------------------------------------------------------- | ------------------------------------------- |
| holds          | **choices** — which model, which provider, which behaviour | **credentials**, and machine-specific paths |
| safe to commit | yes (unless you inline an `api_key`)                       | **never** — gitignored, `0600`              |
| edited by      | `any2wiki setup`, `config set`, or by hand                 | `any2wiki keys set`                         |
| read by        | `src/llm_roles.py`, the tool registries                    | `src/env.py:load_env()` at startup          |
| find it        | `any2wiki config path`                                     | next to it — `<user_root>/.env`             |


**What goes in** `config.yaml` — six blocks, all optional:


| block          | controls                                                                        |
| -------------- | ------------------------------------------------------------------------------- |
| `model`        | the base model for every task: `default`, `provider`, `base_url`, `api_key`     |
| `auxiliary`    | per-task overrides — `title`, `summarize`, `judge`, `subagent`, `web_summarize` |
| `web`          | which search provider to use (firecrawl / tavily / exa)                         |
| `ingest`       | `fast` or `quality` — decides which ingest tools get registered                 |
| `verification` | Loop 2: on/off, `max_iterations`                                                |
| `connectors`   | which connectors `fetch` runs, and their settings                               |


**What goes in** `.env`**:**


| variable                                                      | for                                                                      |
| ------------------------------------------------------------- | ------------------------------------------------------------------------ |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GOOGLE_API_KEY`, …    | whichever provider your model uses — `keys list` shows which one that is |
| `FIRECRAWL_API_KEY` / `TAVILY_API_KEY` / `EXA_API_KEY`        | web search; any one is enough                                            |
| `LANGSMITH_API_KEY`, `LANGSMITH_TRACING`, `LANGSMITH_PROJECT` | tracing — Loop 4 needs it                                                |
| `DAYTONA_API_KEY`                                             | the Marp sandbox; skip with `--eval-mode`                                |
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, `SLACK_CHANNEL_ID`      | `serve` only                                                             |
| `WIKI_PATH`                                                   | where the wiki lives, if not `./wiki`                                    |
| `ANY2WIKI_MODEL`, `ANY2WIKI_MODEL_<TASK>`                     | model overrides that beat `config.yaml` — see below                      |
| `ANY2WIKI_CONFIG`, `ANY2WIKI_HOME`                            | point at a different config file or data directory                       |


### The overlap worth knowing

`config.yaml` accepts an inline `api_key:`, and `.env` accepts model names via
`ANY2WIKI_MODEL*`. Both work, and both are traps:

- **An inline** `api_key` **in** `config.yaml` **is a secret in a file you might commit.** Use it
only for a per-task key on a different provider, and check your `.gitignore`.
- `ANY2WIKI_MODEL*` **in** `.env` **silently outranks** `config.yaml`**.** That is the usual cause
of "I edited the config and nothing changed" — `config show`'s **From** column names the
winner, and a footer lists any env var that is overriding.

## Invoking it

Examples in this document say `any2wiki`. Until an installer puts it on your PATH, the
reliable form is the module path — it works regardless of PATH or editable-install state:

```bash
uv run python -m src.cli.app repl                                  # interactive chat
uv run python -m src.cli.app chat "ingest https://arxiv.org/abs/…" # one-shot, then exit
uv run python -m src.cli.app serve                                 # listen on Slack
uv run python -m src.cli.app fetch git-repo                        # connector fetch, no LLM
uv run python -m src.cli.app sessions ls                           # browse past sessions
uv run python -m src.cli.app config show                           # show effective config
```

Optional alias so it reads like a real command:

```bash
echo "alias any2wiki='uv run python -m src.cli.app'" >> ~/.zshrc && source ~/.zshrc
any2wiki repl
```

(A `any2wiki` console script is also installed in the venv; it needs `.venv/bin` on your
PATH — either `source .venv/bin/activate` or use `uv run any2wiki …`.)

## Commands

**Configuration** — none of these call a model, so they are free and instant:


| Command                    | What it does                                                                               |
| -------------------------- | ------------------------------------------------------------------------------------------ |
| `setup`                    | First-run wizard — provider, model, per-task models, API key ([above](#setup--the-wizard)) |
| `config show`              | Every task's model, provider and endpoint, and which setting won                           |
| `config path`              | Print the config file in use — one pipeable line                                           |
| `config set <key> <value>` | Set one value, e.g. `auxiliary.judge.model openai:gpt-4o-mini`                             |
| `keys list`                | Which API keys are set, masked                                                             |
| `keys set <NAME>`          | Prompt for a key and write it to `.env` (never `config.yaml`)                              |


**Everything else:**


| Command                                                                 | What it does                                                                                                                                        | Needs LLM?                        |
| ----------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------- |
| `repl`                                                                  | Interactive chat session (streaming, approvals, meta-commands)                                                                                      | yes                               |
| `chat "<msg>"`                                                          | Run a single message and exit                                                                                                                       | yes                               |
| `serve`                                                                 | Listen on a Slack channel and run the agent on each message ([below](#slack-serve))                                                                 | yes                               |
| `fetch [connector]`                                                     | Phase 1 of ingest — pull raw source data to `connectors/<name>/`. Omit the name to run every configured connector ([below](#connector-fetch-fetch)) | **no**                            |
| `sessions ls [-n N]`                                                    | List recent sessions, newest first                                                                                                                  | no                                |
| `sessions stats`                                                        | Catalog summary + how many you'd prune at each age threshold                                                                                        | no                                |
| `sessions search "<query>"`                                             | Full-text search message history                                                                                                                    | no                                |
| `sessions resume <id                                                    | title>`                                                                                                                                             | Reopen a past session in the REPL |
| `sessions rename <id                                                    | title> ""`                                                                                                                                          | Rename a session                  |
| `sessions prune [--older-than-days N] [-y]`                             | Delete old ended sessions (+ their checkpoints)                                                                                                     | no                                |
| `sessions prune-orphans [--apply] [--vacuum] [--older-than D] [--full]` | Evict checkpoints with no session row (dry run by default)                                                                                          | no                                |


`sessions`/`config`/`fetch` don't load the agent stack, so they're fast; `repl`/`chat`/`serve`
build the supervisor (and, unless `--eval-mode`, a Daytona sandbox).

## Common flags


| Flag                 | Purpose                                                                                                                                     | Available on                                 |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------- |
| `--model` / `-m`     | Override the **base** model for this run, e.g. `openai:gpt-4o`. Does not move a task pinned in `config.yaml` — see `[MODELS.md](MODELS.md)` | `chat` `repl` `resume` `serve` `config show` |
| `--ingest-mode {fast | quality}`                                                                                                                                   | Override ingest mode                         |
| `--wiki-path`        | Override the wiki directory                                                                                                                 | `chat` `repl` `resume` `serve` `fetch`       |
| `--debug`            | Show diagnostic output (incl. startup timings)                                                                                              | `chat` `repl` `resume` `serve` `fetch`       |
| `--yes` / `-y`       | Auto-approve all approval prompts                                                                                                           | `chat` `repl` `resume` `serve`               |
| `--eval-mode`        | Skip the Daytona sandbox (no Marp subagent)                                                                                                 | `chat` `repl` `resume` `serve`               |
| `--thread-id` / `-t` | Resume / pin a specific thread                                                                                                              | `chat` `repl` `resume`                       |
| `--no-save`          | Don't persist to `sessions.db` (throwaway turns; in-run approvals still work)                                                               | `chat` `repl` `resume`                       |
| `--channel`          | Slack channel id (else `$SLACK_CHANNEL_ID`)                                                                                                 | `serve`                                      |


## Inside the REPL

- Chat in natural language — see [Usage](README.md#usage) in the README for examples.
- **Approvals (HITL):** tool calls pause for review. Choose **a** approve · **e** edit args ·
**r** reject *(with an optional reason sent back to the agent so it tries differently)* ·
**s** respond *(answer on the tool's behalf, for ask-user tools)* · **yolo** approve all for
the session. Only the options a given tool allows are shown.
- **Long tool output** is shown as a short preview; `/open` (or **Ctrl-O** at the prompt)
pages the full output — press `q` to close. *(Ctrl-O works at the* `you ❯` *prompt, not
mid-stream.)*
- **Meta-commands:** `/title <name>` · `/new` · `/help` · `/open` (alias `/last`) · `/exit`
(bare `quit`/`exit`/`bye`/`:q` and Ctrl-D also quit).

## Slack (`serve`)

`serve` is the **same agent against the same wiki**, driven by Slack messages instead of the
terminal (Loop 3). Same `create_supervisor()`, same `wiki/`, same `checkpoints.db` and
`sessions.db` — only the front-end differs.

```bash
any2wiki serve                          # listen on $SLACK_CHANNEL_ID
any2wiki serve --channel C0123456789    # override the channel
any2wiki serve --eval-mode              # no Daytona sandbox (no Marp subagent)
any2wiki serve -y                       # auto-approve — no approval buttons at all
```

**Setup is per-user.** Slack apps are workspace-scoped, so everyone creates their own.
Three env vars, checked at startup:

```bash
SLACK_BOT_TOKEN=xoxb-...   # Bot User OAuth Token   (OAuth & Permissions page)
SLACK_APP_TOKEN=xapp-...   # App-Level Token        (Basic Information page — different page)
SLACK_CHANNEL_ID=C...      # the channel to listen on
```

> **The bot must be** `/invite`**d to the channel — even a public one.** Without it, `serve`
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
Slack tokens set. Message flow and design: `[src/slack/README.md](src/slack/README.md)`.

## Connector fetch (`fetch`)

Phase 1 of ingest, and the only command that never calls an LLM. It hits a source, writes
the raw responses under `connectors/<name>/raw/`, and records every item in a manifest —
then stops. Turning that into wiki pages is a separate agent run, which can be repeated for
free because the raw data is already on disk.

```bash
any2wiki fetch              # run every connector enabled in config.yaml
any2wiki fetch git-repo     # run one
```

Each run reports `N new · N unchanged · N gone`. Unchanged items are skipped by content
hash, so a second run costs almost nothing. Connectors carry their own credentials (or
none — `git-repo` needs nothing), so `fetch` doesn't require `ANTHROPIC_API_KEY` or
`DAYTONA_API_KEY`. Enable one under `connectors:` in `config.yaml`; see
`[src/connectors/README.md](src/connectors/README.md)`.

## Pruning old sessions

History is kept indefinitely by default (it powers `sessions search`). When you want to clean
up, run `sessions stats` first to see the totals, time range, and how many sessions you'd delete
at each age threshold — then prune. **Pruning is manual and explicit** — one command tidies both
stores in lockstep:

```bash
any2wiki sessions stats                      # see totals + what each threshold would delete
any2wiki sessions prune                      # delete ended sessions older than 90 days
any2wiki sessions prune --older-than-days 30 # custom age threshold
any2wiki sessions prune -y                   # skip the confirmation prompt
```

- **Preview before delete:** `prune` lists the date + title of every session it will remove and
asks to confirm, so you can judge each by its title (answer `n` to inspect without deleting).
- **What's removed:** ended sessions past the threshold — their chat history (`sessions.db`)
**and** their resumable graph state (`checkpoints.db`), keyed by the same `thread_id`.
- **What's kept:** active sessions are never pruned, regardless of age.
- **Irreversible:** a pruned session can no longer be searched *or* resumed.

`prune` is the only `sessions` subcommand that loads the checkpointer; `ls` / `search` /
`resume` stay fast.

### Orphan checkpoints

`prune` only evicts checkpoints whose **session row still exists** — it's driven by deleted
sessions. Runs that never wrote a session row (`--no-save` turns, eval/test threads, history
predating `sessions.db`) leave **orphan** checkpoints that `prune` can't reach, and they're
usually the bulk of `checkpoints.db`. Sweep them separately:

```bash
any2wiki sessions prune-orphans                      # dry run — lists orphans + each one's last-activity age
any2wiki sessions prune-orphans --full               # list every orphan (not just the first 20)
any2wiki sessions prune-orphans --older-than 1       # skip threads active in the last day
any2wiki sessions prune-orphans --apply              # actually evict them (via adelete_thread)
any2wiki sessions prune-orphans --apply --vacuum     # also shrink the file on disk
```

- **Dry run by default** — review the list (each orphan shows its last-activity age), then re-run
with `--apply`. Use `--full` to print the whole list instead of the first 20.
- `--older-than DAYS` skips recently-active threads (last activity read from each thread's
most recent checkpoint) — use it to avoid evicting a session mid-first-turn whose session row
hasn't been written yet. Note: **larger values are *more* restrictive** (fewer matches); if it
matches nothing it tells you how many orphans exist and their age range.
- **Safety guard:** if `sessions.db` is empty it refuses (everything would look orphaned).
- `--vacuum` reclaims disk: plain deletes only free pages internally, so the file doesn't
shrink until you VACUUM (a one-shot rebuild). Without it, orphans are gone but the file stays
the same size.

## macOS note

Occasionally (usually right after `uv sync`) the bare `any2wiki` command fails with
`ModuleNotFoundError: No module named 'src'` — a uv editable-`.pth` hidden-flag quirk. Fix:
`chflags nohidden .venv/lib/python*/site-packages/__editable__.llm_wiki-*.pth`. The
`uv run python -m src.cli.app …` form sidesteps it entirely.

---

