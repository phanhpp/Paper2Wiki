# The `paper2wiki` CLI

Every command, flag and workflow. For choosing a model see [`MODELS.md`](MODELS.md); for
how the package is built see [`src/cli/README.md`](src/cli/README.md).

Paper2Wiki ships a terminal CLI (`paper2wiki`) for daily use: an interactive REPL, one-shot
chat, a Slack listener, connector fetches, session browsing, and config inspection. Run
commands **from the repo root** — `.env` is auto-loaded.

## Running it

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

## Commands

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

## Common flags

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

## Inside the REPL

- Chat in natural language — see [Usage](README.md#usage) in the README for examples.
- **Approvals (HITL):** tool calls pause for review. Choose **a** approve · **e** edit args ·
  **r** reject *(with an optional reason sent back to the agent so it tries differently)* ·
  **s** respond *(answer on the tool's behalf, for ask-user tools)* · **yolo** approve all for
  the session. Only the options a given tool allows are shown.
- **Long tool output** is shown as a short preview; **`/open`** (or **Ctrl-O** at the prompt)
  pages the full output — press **`q`** to close. *(Ctrl-O works at the `you ❯` prompt, not
  mid-stream.)*
- **Meta-commands:** `/title <name>` · `/new` · `/help` · `/open` (alias `/last`) · `/exit`
  (bare `quit`/`exit`/`bye`/`:q` and Ctrl-D also quit).

## Slack (`serve`)

`serve` is the **same agent against the same wiki**, driven by Slack messages instead of the
terminal (Loop 3). Same `create_supervisor()`, same `wiki/`, same `checkpoints.db` and
`sessions.db` — only the front-end differs.

```bash
paper2wiki serve                          # listen on $SLACK_CHANNEL_ID
paper2wiki serve --channel C0123456789    # override the channel
paper2wiki serve --eval-mode              # no Daytona sandbox (no Marp subagent)
paper2wiki serve -y                       # auto-approve — no approval buttons at all
```

**Setup is per-user.** Slack apps are workspace-scoped, so everyone creates their own.
Three env vars, checked at startup:

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

## Connector fetch (`fetch`)

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

## Pruning old sessions

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

### Orphan checkpoints

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

## macOS note

Occasionally (usually right after `uv sync`) the bare `paper2wiki` command fails with
`ModuleNotFoundError: No module named 'src'` — a uv editable-`.pth` hidden-flag quirk. Fix:
`chflags nohidden .venv/lib/python*/site-packages/__editable__.llm_wiki-*.pth`. The
`uv run python -m src.cli.app …` form sidesteps it entirely.

---
