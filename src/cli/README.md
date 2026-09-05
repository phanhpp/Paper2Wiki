# `src/cli/` — the terminal front-end

**Turns the agent into a command you can type.** Everything here is about presenting it:
reading flags, drawing output, and closing databases when the run ends. None of the agent's
own logic lives here — `repl` and `serve` build the *same* agent and report through the
*same* `Renderer` methods.

User-facing docs — commands, flags, Slack setup:
`[README.md](../../README.md#using-the-cli)`. The `Renderer` protocol it implements is
defined in `[src/agents/renderer.py](../agents/renderer.py)`.

## How it fits together

What happens when you type `any2wiki repl`:

```
   any2wiki repl
         │
         ▼
   app.py                            reads .env, matches "repl"     (1)
         │
         ▼
   commands/chat.py                  flags → env vars → key check   (2)
         │                           prints the banner
         │                           imports the agent — here, not
         │                           at the top of the file         (3)
         ▼
   _runtime.py  run_async()          opens the event loop, and      (4)
         │                           owns everything below
         ▼
   agents/agent.py                   builds the agent — slow,       (5)
     create_supervisor()             happens once
         │
         ▼
   ┌── one turn ────────────────────────────────────────────────┐
   │                                                            │
   │   agents/stream.py               the agent works, and      │
   │     run_turn_stream_async()      calls Renderer methods    │  (6)
   │             │                                              │
   │             ▼                                              │
   │   renderer.py  RichRenderer      draws them with Rich      │
   │             │                                              │
   │             ▼                                              │
   │       your terminal                                        │
   │                                                            │
   └─────────────────  next prompt  ◄───────────────────────────┘
         │
         ▼
   back in run_async()                closes both databases     (7)
```

1. **`app.py`** — the root callback loads `.env` first, so credentials exist before any
   command runs. Typer then matches the name to a module in `commands/`.
2. **`_env.py`** — `setup_logging` · `apply_env` · `require_keys`. `apply_env` writes your
   flags into `os.environ`, which is what makes a flag beat `config.yaml`.
3. **The import sits mid-function on purpose** — it must come *after* step 2, because the
   tool list is built at import time from those env vars. This is rule 1 below.
4. **`run_async`** (in `_runtime.py`) — opens the event loop and keeps it open for the
   whole run. Steps 5, 6 and 7 all happen inside this one call.
5. **`create_supervisor()`** — the slow step: LangGraph loads and, unless `--eval-mode`, a
   Daytona sandbox is provisioned. `repl` does this **once** and reuses the agent for every
   turn; `chat` does it per invocation, which is why repeated one-shots are slower.
6. **The agent doesn't know it is in a terminal.** It calls `Renderer` methods;
   `RichRenderer` is what turns them into Rich output.
7. **Same `run_async` call, on the way out** — not a new step. When the work finishes (or
   fails, or you press Ctrl-C) it closes both databases in the right order. Rule 2 below.

**With Slack it is the same picture**, and only step 6 differs:

```
   commands/slack.py → src/slack/app.py → SlackRenderer → messages + buttons
```

Steps 1–5 and 7 are identical — same `create_supervisor`, same wiki, same databases. That
is the whole design: swapping the front-end means swapping the class that implements
`Renderer`, nothing else.

## The files


| file          | what it does                                                                                                               |
| ------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `app.py`      | The front door. Lists every command and points it at its module; loads `.env` first.                                       |
| `_env.py`     | The three setup steps every command starts with: set up logging, turn flags into env vars, check the right API keys exist. |
| `_runtime.py` | Runs the agent inside an event loop and closes both databases afterwards, even on Ctrl-C.                                  |
| `renderer.py` | Draws the agent's output in the terminal — streaming text, tool previews, approval prompts.                                |
| `welcome.py`  | The banner printed once when the REPL starts.                                                                              |
| `commands/`   |                                                                                                                            |
| `chat.py`     | `repl` and `chat`. The only file with real logic — it owns the REPL input loop.                                            |
| `slack.py`    | `serve`. Declares the flags; the listener is in `src/slack/`.                                                              |
| `fetch.py`    | `fetch`. Declares the flags; the connectors are in `src/connectors/`.                                                      |
| `sessions.py` | `sessions ls / stats / search / resume / rename / prune / prune-orphans`.                                                  |
| `config.py`   | `config show`.                                                                                                             |


`slack.py` and `fetch.py` are ~50 and ~68 lines — flags at the top, work delegated
elsewhere. Copy that shape for a new command.

## Two rules that are easy to break

**1 · Env before imports.** `src/tools/__init__.py` builds `all_tools` **at import time**
from the resolved ingest mode (`src/ingest_mode.py:get_ingest_mode`). So every command body
runs `apply_env()` *first*, then imports the agent lazily:

```python
setup_logging(debug)
apply_env(ingest_mode, wiki_path)      # writes ANY2WIKI_INGEST_MODE / WIKI_PATH
require_keys(eval_mode)

from src.agents.agent import create_supervisor   # ← only now
```

Hoist that import to the top of the module and `--ingest-mode quality` silently stops
registering `fetch_arxiv` and `parse_pdf_docling`. The same laziness is why
`any2wiki --help` is fast: it never touches the LangGraph import graph.

**2 · Every command that runs the agent goes through** `run_async`**.** Two databases have to
be closed afterwards, and each closes differently: the checkpointer is async, so it has to
close *while the loop is still running* (that is what the inner `_runner_with_cleanup()`
is for); the sessions database is ordinary blocking code, so it closes after the loop
stops. `run_async` does both, even if the run fails or you press Ctrl-C — which it catches
so the CLI exits quietly instead of printing a traceback.

## Why a CLI flag always wins

`apply_env()` doesn't hand your flags down through function arguments — it **writes them
into** `os.environ`, before anything reads config from disk. So every setting resolves in
the same order:

```
CLI flag  →  env var (.env or shell)  →  config.yaml  →  built-in default
```

A flag is just an env var set at the last possible moment. That is what lets you override
config for one run without editing `config.yaml` or `.env`.

Three settings work this way:


| flag             | sets                     | read by                                                   |
| ---------------- | ------------------------ | --------------------------------------------------------- |
| `--ingest-mode`  | `ANY2WIKI_INGEST_MODE` | `src/ingest_mode.py` — decides which tools get registered |
| `--wiki-path`    | `WIKI_PATH`              | the wiki tools                                            |
| `--model` / `-m` | `ANY2WIKI_MODEL`       | `src/llm_roles.py` — the **base** model for every task    |


**One caveat on** `--model`**, and it is deliberate.** It sets the *base* model, so a task with
its own `auxiliary.<task>.model` in `config.yaml` keeps it. On a config that pins cheap
models for the side tasks, `--model` visibly changes only the supervisor::

```
$ any2wiki config show -m openai:gpt-4o
  Model · supervisor      openai:gpt-4o        ← the flag
  Model · judge           claude-haiku-4-5-…   ← auxiliary.judge.model wins
```

This copies hermes-agent, whose config has the same shape
(`cli-config.yaml.example:9` — *"Default model to use, can be overridden with --model
flag"*). Their reasoning, at `:339`: side tasks are chosen for a purpose — vision,
cheap summarisation — and *"not all models/providers support vision, produce usable
summaries, or accept the same API format."* A flag that silently retargeted them would
break things, so neither they nor we offer one.

`config show` prints the resolved model per task, which is how you check what a flag
actually did.

This is also why rule 1 above matters: the flags have to reach `os.environ` *before* the
agent is imported, or the import reads the old values.

## The per-turn UI lifecycle

`RichRenderer` implements the `Renderer` protocol from `src/agents/renderer.py`; the stream
layer calls it and knows nothing about Rich.

```
user hits enter
  → on_turn_start()   transient "Thinking…" spinner
  → on_token() [1st]  spinner stops, a Live Markdown block starts
  → on_token() × N    the block repaints in place
  → on_tool_result()  collapsed preview printed; full text stashed for /open
  → on_turn_end()     Live stops, the rendered Markdown stays on screen
```

Two details that are load-bearing:

- The Live is `transient=True` with `vertical_overflow="crop"`, and `_end_live` commits the
full Markdown once at the end. The earlier `vertical_overflow="visible"` reprinted the
whole block on every refresh, which smeared long output.
- Tool results go to `on_tool_result`, **never** into the Markdown token stream — otherwise
a large tool output would be re-rendered as Markdown on every repaint.

`handle_interrupts` is `async` (it awaits in Slack; the terminal version never actually
awaits, because stdin blocks). Decision shapes come from the shared `build_decisions()`, so
the terminal and Slack cannot drift apart.

## Adding a command

1. Write `commands/<name>.py` with a Typer-annotated function — copy `fetch.py`.
2. Call `setup_logging` → `apply_env` → `require_keys` (skip `require_keys` if it needs no
  model, like `fetch`), *then* import what you need.
3. Register it in `app.py`: `app.command("<name>")(<mod>.<fn>)`, or `app.add_typer(...)` for
  a group.
4. Wrap agent work in `run_async`.
5. Add a row to the README's command table and, if it takes flags, the flags table.



## Known gaps

- **Ctrl-O only works at the** `you ❯` **prompt**, not mid-stream — prompt_toolkit reads keys
only while awaiting input. `/open` (alias `/last`) is the always-available equivalent, and
neither can toggle closed: they launch `less`, so press `q`.
- **Autocompletion is wired but unreachable.** `sessions.py:304,346` pass
`autocompletion=_complete_session_ref`, and `--install-completion` is the documented
way to turn completion on — but `app.py` sets `add_completion=False`, which removes
that flag. Either flip it to `True` or drop the completer.
- **Interactive paths have no automated tests** (spinner, smear, Ctrl-O) — verified by hand
only. Everything else is covered by `tests/test_cli.py` under `pytest -m unit`.

