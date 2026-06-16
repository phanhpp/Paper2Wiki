Let me fetch the key Rich docs pages properly.Good, I have everything I need. Let me write this up properly.

---

## Rich deep-dive for Paper2Wiki

---

### 1. `Console` — what it actually is

`Console` is a stateful object that owns a stream (stdout by default), auto-detects the terminal's color capability, and handles the mechanics of turning Rich renderables into ANSI escape sequences.

```python
from rich.console import Console
console = Console()
```

**Create one instance per stream, reuse it everywhere.** The standard pattern for a CLI is one module-level console:

```python
# paper2wiki/console.py
from rich.console import Console
console = Console()
```

Then everywhere else:
```python
from paper2wiki.console import console
console.print("hello")
```

**Key constructor args you'll actually use:**

```python
Console(
    stderr=False,        # write to sys.stderr instead of sys.stdout
    style="bold red",    # applies to ALL output from this console
    width=80,            # override auto-detected terminal width
    record=True,         # capture all output for later export to SVG/HTML
    force_terminal=True, # force colour even when piped (e.g. CI)
    no_color=True,       # strip all colour (respects NO_COLOR env var)
)
```

The most important behaviour: if Rich detects that it is not writing to a terminal it will strip control codes from the output — so piping to a file gives clean text automatically. You don't need to handle that yourself.

**Key methods on `console`:**

| Method | What it does |
|---|---|
| `console.print(...)` | Main method. Renders markup, pretty-prints objects, word-wraps. |
| `console.log(...)` | Same as print but prepends timestamp + caller file:line. Good for debug. |
| `console.rule("Title")` | Prints a full-width horizontal line with optional centred title. |
| `console.out(...)` | Low-level — no markup, no pretty-print, no word-wrap. Raw text + optional style. |
| `console.print_json(...)` | Pretty-prints a JSON string with syntax highlighting. |
| `console.status("Working...")` | Spinner animation as a context manager (see below). |

`console.rule` is specifically useful for Paper2Wiki — use it to visually separate agent output sections:

```python
console.rule("[bold magenta]Ingest Complete")
# prints: ──────────────────── Ingest Complete ────────────────────
```

---

### 2. Console Markup — what `[/]` means

Console markup uses a syntax inspired by bbcode. If you write the style in square brackets, e.g. `[bold red]`, that style will apply until it is closed with a corresponding `[/bold red]`.

There is a shorthand for closing a style. If you omit the style name from the closing tag, Rich will close the last **opened** style. That's what `[/]` is — close the most recently opened tag.

```python
console.print("[bold red]error[/bold red] occurred")  # explicit close
console.print("[bold red]error[/] occurred")           # shorthand — same result
console.print("[bold red]error[/] occurred")           # [/] closes [bold red]
```

Tags can be **combined in one bracket** — they're space-separated:
```python
console.print("[bold italic yellow on red]impossible to read[/]")
# bold + italic + yellow fg + red bg
```

Tags **don't need to be strictly nested** — overlapping is fine:
```python
console.print("[bold]Bold[italic] bold and italic [/bold]italic only[/italic]")
```

**Styles you can use:**

```
bold, dim, italic, underline, blink, strike
red, green, blue, yellow, magenta, cyan, white, black
bright_red, bright_green, ... (bright variants)
on red, on blue, ...         (background colours)
rgb(255,0,0)                 (true colour — most modern terminals)
#ff0000                      (hex — same)
```

**Emoji codes work too:**
```python
console.print(":sparkles: Paper2Wiki :sparkles:")  # → ✨ Paper2Wiki ✨
```

**Escaping** — if you're printing user input that might contain `[`, escape it:
```python
from rich.markup import escape
console.print(f"User said: {escape(user_input)}")
```

**Disable markup parsing entirely** for a specific print call:
```python
console.print("[this is not markup]", markup=False)
```

---

### 3. `Panel` — the bordered box

`Panel` draws a border around text or other renderable. Construct it with the renderable as the first positional argument.

```python
from rich.panel import Panel
console.print(Panel("Hello, [red]World!"))
```

Panels extend to the full width of the terminal by default. You can make a panel fit the content by setting `expand=False` on the constructor, or by creating the Panel with `Panel.fit()`.

```python
Panel("content")           # full terminal width
Panel("content", expand=False)  # shrinks to content width
Panel.fit("content")       # same as expand=False, convenience classmethod
```

**Key constructor args:**

```python
Panel(
    renderable,              # str (parsed as markup), Text, Table, Group, etc.
    title="My Title",        # text in the top border — supports markup
    subtitle="v0.1",         # text in the bottom border — supports markup
    border_style="magenta",  # colour/style of the border lines
    padding=(1, 2),          # (top+bottom, left+right) padding inside border
    expand=True,             # True = full width, False = fit content
    box=box.ROUNDED,         # border character set (see below)
)
```

**Box styles** — import from `rich.box`:

```python
from rich import box

box.ROUNDED      # ╭──╮  ← Claude Code look, soft corners
box.SQUARE       # ┌──┐  ← sharp corners
box.DOUBLE       # ╔══╗  ← double lines
box.HEAVY        # ┏━━┓  ← thick lines
box.SIMPLE       # no corners, just top/bottom lines
box.MINIMAL      # just spaces, almost invisible
```

---

### 4. `Live` — what it is and when NOT to use it

`Live` is a class for animating parts of the terminal — progress bars and status indicators use it internally. You can build custom live displays with the `Live` class.

The key mental model: **Live takes over a region of the terminal and redraws it in place**. Everything inside the `with Live(...)` block is redrawn at `refresh_per_second`. Normal `console.print()` calls during Live appear *above* the live region and scroll up as new lines are added.

```python
from rich.live import Live
from rich.table import Table

table = Table()
table.add_column("Step")
table.add_column("Status")

with Live(table, refresh_per_second=4):
    for step in steps:
        table.add_row(step.name, "[yellow]running")
        do_work(step)
        # mutate the table in-place — Live redraws it automatically
```

You can also change the renderable on-the-fly by calling the `update()` method. This is useful if the information you want to display is too dynamic to update a single renderable in place.

```python
with Live(generate_status(), refresh_per_second=4) as live:
    for step in steps:
        live.update(generate_status())  # replace the whole renderable
```

The Live class creates an internal Console object accessible via `live.console`. If you print or log to this console, the output appears above the live display.

```python
with Live(table, refresh_per_second=4) as live:
    live.console.print("[green]Starting ingest...")  # goes above the table
```

**`transient=True`** — the live region disappears when the context exits, leaving a clean terminal:

```python
with Live(spinner_panel, transient=True):
    do_long_work()
# after context: live region gone, cursor at previous position
```

**For Paper2Wiki — do you actually need Live?**

For the welcome screen: **no**, `Live` is overkill. The welcome banner is static — print it once and move on to the `prompt_toolkit` loop.

Live is useful for Paper2Wiki in these specific cases:
- Showing ingest progress (steps completing one by one in an updating Table)
- Showing a spinner while the agent is thinking between tool calls
- Showing trace-analyzer output as it processes runs in parallel

For a spinner during agent thinking, `console.status()` is simpler than `Live` directly — it wraps Live internally:

```python
with console.status("[cyan]Agent thinking...", spinner="dots"):
    result = await agent.ainvoke(inputs)
```

---

### 5. The welcome screen for Paper2Wiki

Now putting it all together — what you actually need for a Claude Code / Hermes-style banner:

```python
# paper2wiki/cli/welcome.py
from rich.console import Console
from rich.panel import Panel
from rich.text import Text
from rich import box

console = Console()

def print_welcome():
    # Title line — Text object lets you mix styles more precisely than markup
    title = Text()
    title.append("Paper", style="bold magenta")
    title.append("2", style="bold white")
    title.append("Wiki", style="bold magenta")
    title.append("  ", style="")
    title.append("self-improving paper → wiki agent", style="dim white")

    # Body — plain markup string is fine here
    body = (
        "[bold white]Commands[/]\n\n"
        "  [cyan]/ingest[/]  [dim]<arxiv_id|url>[/]   ingest paper into wiki\n"
        "  [cyan]/query[/]   [dim]<question>[/]        ask the wiki\n"
        "  [cyan]/health[/]                      lint + check wiki\n"
        "  [cyan]/improve[/]                     run trace-analyzer\n"
        "  [cyan]/help[/]                        show all commands\n\n"
        "[dim]ctrl-d or /quit to exit[/]"
    )

    console.print(Panel(
        body,
        title=title,
        subtitle="[dim]sonnet supervisor · haiku subagent[/]",
        border_style="magenta",
        box=box.ROUNDED,        # soft corners like Claude Code
        padding=(1, 3),
        expand=False,            # shrink to content, don't fill full width
    ))

    console.print()  # blank line before prompt
```

Then in your `main()` / `__main__`:

```python
from paper2wiki.cli.welcome import print_welcome, console
from prompt_toolkit import PromptSession

def main():
    print_welcome()   # Rich prints the banner to stdout

    session = PromptSession()  # prompt_toolkit takes over stdin
    while True:
        try:
            text = session.prompt("› ")
        except EOFError:
            break
        handle_command(text)
```

The split stays clean: `print_welcome()` → Rich → stdout. Then `PromptSession` → prompt_toolkit → stdin. They don't interfere because they operate on opposite ends.

**If you later want a persistent header** (stays visible while output scrolls below it) that's when you'd use `Live(screen=True)` combined with `Layout` — but that's a significant step up in complexity and you don't need it for phase 1. Start with the static banner.