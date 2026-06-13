# Guide: Shell autocompletion for the `paper2wiki` CLI

Typer gives two tiers of tab-completion:

1. **Built-in** (free) — completes command names, option names, and `Enum` choices.
2. **Custom** (you write it) — completes the *values* of an argument/option dynamically
   (e.g. real thread IDs pulled from the DB).

This doc covers both, the gotchas specific to this project, and the one place custom
completion is genuinely worth adding here.

## 1. Built-in completion — enable it once

Because the project is packaged (`[project.scripts] paper2wiki = ...`), Typer can inject a
completion script into your shell:

```bash
paper2wiki --install-completion     # writes to ~/.zshrc (or bash/fish equivalent)
exec $SHELL                          # reload the shell
```

After that, these all work with no extra code:

```
paper2wiki <TAB>                  → repl  chat  sessions  config
paper2wiki sessions <TAB>         → ls  search  resume  prune
paper2wiki chat --in<TAB>         → --ingest-mode
paper2wiki chat --ingest-mode <TAB> → fast  quality      (Enum choices, automatic)
```

`--install-completion` only works with the installed `paper2wiki` command, not
`python -m src.cli.app`. (On macOS this needs uv ≥ 0.11 — see `docs/cli.md`.)

## 2. Custom value completion — anatomy

Pass a callback to `autocompletion=` on a `typer.Option` / `typer.Argument`. Typer injects
arguments **by type annotation**, so you declare only what you need (order/names don't matter):

| Parameter type | Typer injects |
|---|---|
| `str` | the **incomplete** text typed so far (filter with `.startswith(incomplete)`) |
| `typer.Context` | parsed state — `ctx.params` holds already-entered values (de-dup multi-value options) |
| `list[str]` | the raw shell argument array (advanced/debug) |

```python
from typing import Annotated
import typer

VALID = ["fast", "quality"]

def complete_mode(incomplete: str):
    for v in VALID:
        if v.startswith(incomplete):
            yield v                       # plain value
            # or: yield (v, "help text")  # value + description shown in the menu

@app.command()
def main(mode: Annotated[str, typer.Option(autocompletion=complete_mode)] = "fast"):
    ...
```

`yield (value, help_text)` shows a description next to each suggestion in shells that support
it (zsh/fish).

## 3. The one critical rule: never `print()` inside a completer

The shell reads the completer's **stdout** as the menu payload. A stray `print()` corrupts it
and breaks completion silently. To debug, write to **stderr**:

```python
from rich.console import Console
err_console = Console(stderr=True)

def complete_thing(ctx: typer.Context, args: list[str], incomplete: str):
    err_console.print(f"[debug] raw args: {args}")   # safe — goes to stderr
    ...                                                # only yield real values to stdout
```

## 4. Where it's worth it in *this* CLI

| Target | Built-in covers it? | Custom worth it? |
|---|---|---|
| command names (`sessions`, `config`) | ✅ yes | no |
| option names (`--ingest-mode`) | ✅ yes | no |
| `--ingest-mode` values (`fast`/`quality`) | ✅ Enum auto | no |
| **`sessions resume <thread_id>`** | ❌ | **yes — high value** |
| `sessions ls --source` values | ❌ | nice-to-have |
| `--wiki-path` | shell path completion (if typed as a path) | no |

The standout is **`sessions resume`**: thread IDs are UUID7 strings nobody can type or
remember, and the DB already knows all of them with their titles. This turns
`paper2wiki sessions resume <TAB>` into a picker.

### Recommended implementation (thread-id completer)

```python
# src/cli/commands/sessions.py
def _complete_thread_id(incomplete: str):
    """Complete thread IDs from sessions.db (newest first), title shown as help."""
    try:
        from src.sessions.sessions_db_setup import get_sessions_conn
        rows = get_sessions_conn().execute(
            "SELECT id, title FROM sessions ORDER BY started_at DESC LIMIT 50"
        ).fetchall()
    except Exception:
        return                      # never crash the shell; just offer nothing
    for sid, title in rows:
        if sid.startswith(incomplete):
            yield (sid, title or "untitled")

@app.command("resume")
def resume(
    thread_id: Annotated[str, typer.Argument(
        help="Thread ID to resume (see `sessions ls`).",
        autocompletion=_complete_thread_id,
    )],
    ...
):
    ...
```

Optionally the same pattern for `ls --source` (yield `DISTINCT source` from `sessions`).

## 5. Performance gotcha (important here)

A completer runs in a **fresh subprocess on every TAB**, so its import cost is the latency the
user feels. Two rules:

- **Import lazily inside the completer** (as above) — never at module top — so unrelated heavy
  imports don't load. Querying `sessions.db` is a cheap SQLite read; fine.
- Note that importing the app pulls in `commands/chat.py`, which currently imports
  `langchain_core.utils.uuid` at module top. That's loaded on every completion. If completion
  feels sluggish, move that import into the function that uses it (`_new_thread_id`).

## 6. Testing completers

Call the function directly in a unit test — no shell needed:

```python
def test_complete_thread_id(monkeypatch):
    # monkeypatch get_sessions_conn to return a fake conn with known rows
    results = list(_complete_thread_id("01"))
    assert all(sid.startswith("01") for sid, _ in results)
```

Do **not** assert on stdout/stderr; assert on the yielded values.
