# Paper2Wiki CLI — implementation notes

Wraps the supervisor agent in a terminal CLI (`paper2wiki`) covering daily use: an
interactive REPL, one-shot chat, session browsing, and config inspection. Built with
**Typer** (command scaffolding), **Rich** (streamed output), and **prompt_toolkit**
(interactive input).

## How to run

The `[project.scripts]` entry installs a `paper2wiki` command (activate the venv with
`source .venv/bin/activate`, or prefix with `uv run`). The app auto-loads `.env`.

```bash
paper2wiki repl                                       # interactive chat
paper2wiki chat "ingest https://arxiv.org/abs/..."    # one-shot
paper2wiki sessions ls                                # browse sessions (no agent/LLM)
paper2wiki config show                                # effective config
```

`python -m src.cli.app <cmd>` is an equivalent invocation that doesn't depend on the
installed entry point.

> **macOS + old uv caveat (resolved on uv ≥ 0.11):** earlier uv (e.g. 0.6.x) created the
> editable `.pth` with the macOS `UF_HIDDEN` flag, and CPython's `site` silently skips
> hidden `.pth` files — so `import src` failed and the `paper2wiki` console script broke
> (the flag was re-applied on every `uv sync`). Fix is to `uv self update` (≥ 0.11 no longer
> sets the flag); as a stopgap, `python -m src.cli.app` always works, or
> `chflags nohidden .venv/lib/python*/site-packages/__editable__.llm_wiki-*.pth`.

Common flags (on `chat` / `repl` / `sessions resume`):

| Flag | Purpose |
|---|---|
| `--thread-id` / `-t` | Resume / pin a specific thread |
| `--ingest-mode {fast\|quality}` | Override ingest mode (sets env before tool registration) |
| `--wiki-path` | Override the wiki directory |
| `--yes` / `-y` | Auto-approve all HITL prompts |
| `--eval-mode` | Skip the Daytona sandbox (no marp subagent) |
| `--debug` | Show diagnostic output |

REPL meta-commands: `/new` (fresh thread + sandbox), `/help`, `/exit` (also Ctrl-D).

## Architecture

```
                        python -m src.cli.app
                                 │
                         src/cli/app.py  (Typer app, loads .env)
            ┌────────────────────┼─────────────────────┐
            ▼                    ▼                       ▼
   commands/chat.py     commands/sessions.py     commands/config.py
   (repl, chat)         (ls/search/resume/prune) (show)
            │                    │
            │ apply_env() ──► PAPER2WIKI_INGEST_MODE / WIKI_PATH   (src/cli/_env.py)
            │ require_keys()                                       (src/cli/_env.py)
            ▼
   run_async(coro)  ── try/finally ─► close_checkpointer + close_sessions_conn
   (src/cli/_runtime.py)
            │
            ▼
   create_supervisor(thread_id, eval_mode)         (src/agents/agent.py, unchanged)
   run_turn_stream_async(..., renderer=RichRenderer)
            │
            ▼
   RichRenderer  ── implements ──►  Renderer protocol
   (src/cli/renderer.py)            (src/agents/renderer.py)
```

Key design point: **`apply_env()` runs before the agent is imported.** `src/tools/__init__.py`
builds `all_tools` at *import* time from the resolved ingest mode, so the command bodies set
the env vars first and import `create_supervisor` lazily.

The REPL builds the supervisor **once** (it spins up a thread-scoped Daytona sandbox) and
reuses it across turns by passing `agent=` into every `run_turn_stream_async` call.

## Command structure: leaf commands vs groups

Typer has exactly two building blocks:

| Building block | What it is | How you make it |
|---|---|---|
| **Command** (leaf) | a thing you can run | `@app.command()` / `app.command("name")(fn)` |
| **Group** (namespace) | a thing that *contains* commands | a separate `typer.Typer()`, mounted with `app.add_typer(sub, name=...)` |

A `typer.Typer()` instance **is** a group. To get a nested path like `paper2wiki sessions ls`
you need a container for `ls`, `search`, … — and that container is its own `Typer`. The
subcommands must be registered on *something*, and that something is a `Typer`. That's why
`src/cli/app.py` wires it like this:

```python
app.command("repl")(chat_cmd.repl)                 # leaf → directly on the app
app.command("chat")(chat_cmd.chat)                 # leaf → directly on the app
app.add_typer(sessions_cmd.app, name="sessions")   # group → mount the sub-Typer
app.add_typer(config_cmd.app,   name="config")     # group → mount the sub-Typer
```

```
paper2wiki
├── repl                  (leaf command)
├── chat                  (leaf command)
├── sessions  ◄── its own Typer ──┐  ls / search / resume / prune
└── config    ◄── its own Typer ──┘  show
```

- `repl` / `chat` are **single actions** → one function each → live on the main app.
- `sessions` has **four actions** → they need a parent → `sessions_cmd.app` is that parent.

### Why `config` is a group despite having only `show`

`config` could be a single leaf command (`paper2wiki config`), but it's a group on purpose:

1. **Namespacing / room to grow** — `config show` reserves `config` as a category, so adding
   `config set`, `config path`, … later is non-breaking (no rename after users learn it).
2. **Parallel structure** — `sessions` and `config` are both "inspect/manage" namespaces;
   a consistent `<noun> <verb>` shape is more predictable than `sessions ls` + a bare `config`.
3. **Avoids a Typer gotcha** — a `Typer` with exactly one command and no callback can
   "collapse" so the subcommand name becomes optional, which makes `config` vs `config show`
   behave inconsistently. Mounting it as an explicit sub-`Typer` keeps `config show` predictable.

If `config` will only ever do one thing, flattening to `app.command("config")(config_show)` is
fine; the group form costs one line and future-proofs the command surface.

## Files added

| File | Purpose |
|---|---|
| `src/agents/renderer.py` | `Renderer` protocol + `DefaultRenderer` (the original `print()` behavior) + shared `build_decisions()` HITL loop. Lets the stream loop drive both notebooks and the CLI without forking logic. Auto-approve is now per-renderer instance state, not a module global. |
| `src/cli/__init__.py` | Package marker. |
| `src/cli/app.py` | Typer entry point. Registers `repl`, `chat`, `sessions`, `config`; loads `.env` in the root callback; `main()` sets `prog_name="paper2wiki"` for `python -m`. |
| `src/cli/_runtime.py` | `run_async(coro)` — owns the event loop and guarantees `close_checkpointer()` (async) + `close_sessions_conn()` (sync) on exit, swallowing Ctrl-C. |
| `src/cli/_env.py` | `IngestMode` enum, `apply_env()` (flags → env vars), `require_keys()` (fail fast on missing `ANTHROPIC_API_KEY`; `DAYTONA_API_KEY` unless `--eval-mode`). |
| `src/cli/renderer.py` | `RichRenderer` — streams assistant text as live Markdown (`rich.live.Live`), prints styled tool-call lines, renders HITL prompts in a Rich panel. |
| `src/cli/commands/__init__.py` | Package marker. |
| `src/cli/commands/chat.py` | `repl` (interactive loop, prompt_toolkit input, `/new` `/help` `/exit`) and `chat` (one-shot). Shared `run_repl()` / `run_chat()` helpers so `sessions resume` can reuse the REPL. |
| `src/cli/commands/sessions.py` | `ls`, `search` (FTS5 over `messages_fts`), `resume` (→ REPL), `prune`. Queries `sessions.db` directly; renders Rich tables. |
| `src/cli/commands/config.py` | `show` — resolved ingest mode, wiki path, available web providers. |
| `tests/test_cli.py` | 12 unit tests: HITL decision mapping (approve/reject/edit-JSON/edit-dict/invalid/yolo/auto-approve) and env/key resolution. Pure, no I/O. |
| `docs/cli.md` | This document. |

## Files changed

| File | Change |
|---|---|
| `src/agents/stream.py` | Output now goes through a `Renderer`. Removed the `SESSION_AUTO_APPROVE` module global and the inline `_handle_interrupts` (moved to `renderer.py`). `run_turn_stream_async` / `run_turn_stream` gained `renderer` / `auto_approve` / `debug` params; defaults build a `DefaultRenderer`, so notebook callers are unaffected. The previously-unconditional `[debug] session message content types` print is now gated behind `--debug` (`renderer.on_debug`). |
| `pyproject.toml` | Added `typer`, `rich`, `prompt-toolkit` deps; `[project.scripts] paper2wiki = "src.cli.app:app"`; setuptools `[build-system]` + `[tool.uv] package = true` + `[tool.setuptools.packages.find] include = ["src*"]` so the project is installable. |
| `CLAUDE.md` | Added CLI usage to *Setup & Commands* (incl. the macOS `python -m` note) and marked the "Wrap agent into CLI" todo done. |
| `uv.lock` | Locked the new dependencies. |

## Fixed alongside the CLI: idempotent session saves

`run_turn_stream_async` re-saves the **entire** accumulated thread message history at the
end of every turn. With random per-row UUIDs, a multi-turn REPL thread duplicated earlier
messages into `messages` (and, via the `messages_fts_insert` trigger, into the FTS index) —
growing quadratically and polluting `sessions search`. The CLI made this easy to hit.

Fix (`src/sessions/session_manager.py`): message rows now use a **deterministic id**
(`_stable_message_id` = `sha256(thread_id, position, role, content)[:32]`) with
`INSERT OR IGNORE`. Re-saving a thread is now a no-op for rows already written, so both the
table and its FTS index stay duplicate-free. Covered by `tests/test_session_manager.py`.
