# The `Renderer` protocol — what it is and how it's used

`Renderer` is the piece that decouples *"run the agent and produce events"* from
*"show those events to a human."* It lives in `src/agents/renderer.py` and lets the same
agent streaming loop drive both the Jupyter notebook and the CLI.

## 1. What a `Protocol` is (the Python concept)

A `typing.Protocol` is a **contract that says "any object with these methods counts."** It's
structural typing — a class does *not* have to inherit from it. If a class happens to have the
right methods, it *is* a `Renderer` as far as type-checkers are concerned (like a Java
interface, but nobody declares `implements Renderer`).

```python
# src/agents/renderer.py
@runtime_checkable
class Renderer(Protocol):
    def on_token(self, text: str) -> None: ...
    def on_tool_call(self, name: str, args: Any) -> None: ...
    def on_turn_end(self) -> None: ...
    def on_debug(self, message: str) -> None: ...
    def handle_interrupts(self, interrupts: list) -> list[dict]: ...
```

This class has **no real code** — the `...` bodies are placeholders. It's purely a
description: "a Renderer is anything that can do these 5 things."

## 2. Why it exists here

Before this design, `stream.py` had `print(...)` calls hard-wired into the agent loop. That's
fine for a notebook, but the CLI needs Rich-formatted output and nicer HITL prompts. Instead of
copy-pasting the whole streaming loop into a "CLI version," the loop now **emits events** and
lets someone else decide how to display them.

The loop doesn't know or care whether output goes to a Jupyter cell or a Rich terminal — it
just calls `renderer.on_token("hello")`. Two different objects answer that call differently:

| Renderer | Where | `on_token` does… | `handle_interrupts` reads via… |
|---|---|---|---|
| `DefaultRenderer` | notebook / tests | `print(text, end="")` | plain `input()` |
| `RichRenderer` | the CLI | live-updating Markdown via `rich` | Rich panel + `rich.prompt.Prompt` |

Both satisfy the `Renderer` protocol (they have all 5 methods), so the loop accepts either.

## 3. How it's wired into the loop

`src/agents/stream.py` picks a default renderer if you don't pass one, so notebook callers are
unaffected:

```python
async def run_turn_stream_async(user_message, ..., renderer=None, auto_approve=False, debug=False):
    if renderer is None:
        renderer = DefaultRenderer(auto_approve=auto_approve, debug=debug)
```

Inside the streaming loop, every place that used to `print` now calls a method on `renderer`:

```python
renderer.on_token(text)                          # was: print(text, end="")
renderer.on_tool_call(tc["name"], tc["args"])    # was: print(f"🔧 {tc['name']}...")
renderer.on_turn_end()                           # was: print()  (trailing newline)
decisions = renderer.handle_interrupts(pending)  # was: inline input() prompt
renderer.on_debug("[debug] ...")                 # only shown with --debug
```

The CLI supplies its own renderer (`src/cli/commands/chat.py`):

```python
from src.cli.renderer import RichRenderer
renderer = RichRenderer(auto_approve=yes, debug=debug)
await run_turn_stream_async(user_in, agent=agent, thread_id=..., renderer=renderer)
```

So:

- given a `RichRenderer`, polished terminal UI
- given nothing (like in a notebook), it `print`s exactly like before.

## 4. The flow, concretely

```
run_turn_stream_async  (the agent loop — display-agnostic)
        │
        │  "a token!"        renderer.on_token("Hel")
        │  "a token!"        renderer.on_token("lo")
        │  "calling a tool"  renderer.on_tool_call("write_file", {...})
        │  "need approval"   renderer.handle_interrupts([...]) ──► [{"type":"approve"}]
        │  "turn done"       renderer.on_turn_end()
        ▼
   renderer  (the ONLY thing that knows about screens/keyboards)
   ├─ DefaultRenderer → print(...) / input(...)
   └─ RichRenderer    → rich.Live Markdown / Rich panel
```

The loop is the *"what happened"*; the renderer is the *"how to show it."* Swapping renderers
swaps the entire UI without touching the agent logic.

## 5. Shared HITL logic: `build_decisions` + `_AutoApprove`

The approve / edit / reject logic is identical for both renderers — only *how you ask the user*
differs (`DefaultRenderer` uses plain `input()`; `RichRenderer` uses a styled
`rich.prompt.Prompt.ask` with validated choices). So that shared logic lives in one function,
`build_decisions(...)`, in `renderer.py`. Each renderer's `handle_interrupts` passes in two
small callbacks plus a shared flag holder:

- `prompt_fn(action, review_config)` — how to ask, returns the lowercased choice (`a`/`e`/`r`/`yolo`)
- `edit_fn(action)` — how to read the edited args string
- `_AutoApprove` — a tiny mutable object remembering whether the user typed `yolo`, so
  auto-approve sticks for the rest of the session

This keeps the parse-the-choice logic from being duplicated across renderers.

## In one sentence

`Renderer` is a contract listing the 5 display events the agent loop produces;
`DefaultRenderer` and `RichRenderer` are two implementations of that contract, and the loop
calls the contract's methods without knowing which one it's talking to — that's what lets the
same code drive both the notebook and the CLI.

## Adding a new front-end

To target a new surface (e.g. a web socket, a Slack bot, a TUI), write a class with the same 5
methods and pass an instance as `renderer=`. No change to `stream.py` is required.
