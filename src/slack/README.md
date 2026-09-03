# `src/slack/` — the Slack front-end (Loop 3)

Message the bot in Slack and it runs **the same agent, against the same wiki**, as
your terminal does. Same `create_supervisor()`, same `wiki/`, same `checkpoints.db`
and `sessions.db`.

Setup (Slack app, tokens, inviting the bot): `docs/slack_setup.md`.
Design rationale and transport comparison: `docs/loop3_slack.md`.

## The libraries, first

**Typer** — turns Python functions into CLI commands. You write a function with type
hints; Typer generates the `--help`, parses `--flags`, and validates types. That's why
`serve` became a real command in ~45 lines: the
`Annotated[bool, typer.Option("--yes", "-y", ...)]` annotations *are* the CLI
definition.

**Rich** — draws nice things in a terminal. `RichRenderer` uses seven pieces: `Console`
(output), `Live` (a block that repaints in place — how streaming text updates without
scrolling), `Markdown`, `Panel` (the boxed approval prompt), `Prompt` (asks a
question), `Spinner` (the "Thinking…"), `Text` (styled strings).

**Neither has anything to do with Slack.** That separation is the whole point of the
next section.

## The Renderer protocol — what it is and why it exists

`src/agents/renderer.py` defines a **contract**: seven methods a front-end must have.

```python
class Renderer(Protocol):
    def on_turn_start(self)                          # agent started thinking
    def on_token(self, text)                         # a chunk of answer arrived
    def on_tool_call(self, name, args)               # agent is calling a tool
    def on_tool_result(self, name, content)          # tool returned
    def on_turn_end(self)                            # done streaming
    def on_debug(self, message)                      # diagnostics
    async def handle_interrupts(self, interrupts)    # ask permission → decisions
```

`stream.py` runs the agent and calls those seven methods. **It never imports Rich,
never imports Slack.** It doesn't know where output goes.

`Protocol` means structural typing — no inheritance, no base class. Any object with
those seven methods *is* a Renderer. That's why `SlackRenderer` needed zero changes to
the agent, streaming, or persistence layers.

```
                    run_turn_stream_async()
                    calls 7 methods, knows nothing else
                              │
          ┌───────────────────┼───────────────────┐
          ▼                   ▼                   ▼
   DefaultRenderer      RichRenderer        SlackRenderer
   print()              Rich + Typer        slack-bolt
   notebooks/tests      your terminal       your Slack
```

Three siblings, no hierarchy. Swapping Rich → Textual later replaces the middle one and
touches neither of the others.

**Only** `handle_interrupts` **is async.** The other six are sync everywhere. It has to be
async because the answer doesn't always arrive on the calling thread: a terminal blocks
on stdin, but Slack gets the click later, as a separate event, and must await it without
freezing the loop. `build_decisions()` is async for the same reason — its three
callbacks are awaited.

## The files


| File          | What it does                                                                           |
| ------------- | -------------------------------------------------------------------------------------- |
| `threads.py`  | Slack thread → LangGraph `thread_id`. Pure functions, no I/O.                          |
| `renderer.py` | `SlackRenderer` — posts the agent's output into Slack.                                 |
| `app.py`      | Socket Mode listener: receives messages, runs turns one at a time, routes clicks back. |




## How one message becomes a reply

```
you type in Slack
      │
      ▼
1. app.py picks it up
      drops anything that isn't a real user message in your channel
      replies "🤔 On it…" straight away  (a new sandbox takes ~30s)
      works out the thread id, adds the job to a queue
      │
      ▼
2. the worker takes the job
      one at a time — the next job waits until this one is finished
      │
      ▼
3. the agent runs
      run_turn_stream_async() — the same function the terminal uses
      │
      ▼
4. SlackRenderer posts the result
      the answer arrives in small pieces — they all go into one Slack
      message, which updates as it goes
      if a tool needs approval, it posts buttons and waits for a click
```

It all runs on one event loop. No threads.

## Three things that explain the design

**Why** `thread_id` **isn't stored anywhere.** Slack identifies a conversation with a
channel + a timestamp; LangGraph identifies one with a single `thread_id` string.
`thread_id_for()` is the translation, and it's pure string formatting — no lookup
table, nothing to write before the first turn, nothing to keep in sync.

**Deriving it** — the reply lands on the same id, which is what makes a Slack thread
a conversation:

```
new message      channel "C123",  ts "111.1",  thread_ts None
                     └─► thread_id_for()  ─►  "slack-C123-111.1"

threaded reply   channel "C123",  ts "222.2",  thread_ts "111.1"
                     └─► thread_id_for()  ─►  "slack-C123-111.1"   ← same id
```

`thread_id_for()` uses `thread_ts` when present, and falls back to the message's own
`ts` when it's a new top-level message.

**Using it** — both databases are keyed on that id, but do unrelated jobs:

```
"slack-C123-111.1"
      │
      ├─► checkpoints.db    LangGraph. Read + write. THIS is the memory —
      │                     same id means the agent resumes the thread.
      │
      └─► sessions.db       Not LangGraph. Write-only, after the turn.
                            Powers `sessions ls` and full-text search.
```

Delete `sessions.db` and the agent still remembers everything (you just lose browsable
history). Delete `checkpoints.db` and every conversation forgets itself.

A separate helper, `reply_ts_for()`, answers a different question — *where to post* —
so the bot's replies land in the thread the message came from instead of the channel.

**Why turns run one at a time.** Send two messages quickly and, without a queue, two
turns run at once. Two things break:

- **The databases.** SQLite allows one writer. The second turn errors.
- **Loop 2 blames the wrong run.** It works out what a run wrote by listing the wiki
files before and after. If turn B writes while turn A is still going, A's "after"
list includes B's files — so A is checked for pages it never wrote, and fails.

So one worker task handles the queue. It finishes a turn completely — including waiting
for approval — before starting the next.

**Why HITL waits on a Future.** In the terminal, the agent asks a question and waits for
you to type. In Slack, the answer arrives later as a separate event, so it can't just
wait in place.

Instead it uses an `asyncio.Future` — an empty box that someone fills in later.
`await`ing an empty one means "pause here, and let everything else keep running until
there's something in the box":

```
handle_interrupts   posts the buttons, makes an empty box, awaits it → paused
                    (nothing is blocked — Slack can still be listened to)
you click Approve
submit_decision()   puts ("a", "") in the box
handle_interrupts   wakes up with ("a", ""), returns {"type": "approve"}
```

A plain blocking wait would hold the whole loop, so the click could never arrive —
that's the deadlock this avoids.

What comes back — approve / reject / edit — is built by `build_decisions()` in
`src/agents/renderer.py`, which the terminal uses too. Shared, so the two can't disagree.

## What gets posted

One tool call used to mean one Slack message, which buried the answer under 30+ of them
on a single ingest. Instead there's **one status line that keeps changing**:

```
🤔 On it…                         posted by app.py before the agent starts
🔧 read_file  {...}               edited in place, one per tool call
🔧 fetch_arxiv {...}
✅ 2 steps   [View details]       settles when the answer starts
Ingested the paper.               the answer, its own message
```

Tool *results* aren't posted at all — they're collected into a step log behind the
**View details** button, which opens a modal. Slack has no collapsible section, so a
modal is the only real "expand". Results are truncated to 6 lines / 400 chars each, and
the modal caps at Slack's 3000-char block limit.

Step logs are kept in memory, capped at the last 50 turns (`_MAX_STEP_LOGS`), so a
long-running `serve` can't grow without bound. Click **View details** on an older turn
and you get "that step log has expired" rather than a crash.

## Known limits

- **No** `edit` **or** `respond` **in Slack.** Both need typed *arguments*, which would mean a
modal built per tool schema. Tools allowing only those are rejected with a note to use
the terminal. Reject *reasons* do work — that's a plain text modal.
- **Answers only while** `serve` **is running.** A laptop process, not a service.
- **One channel**, by design (`SLACK_CHANNEL_ID`) — a stray mention elsewhere shouldn't
kick off a real ingest.



## Tests

`tests/test_slack.py` — 25 unit tests, no network, no workspace: thread-id derivation,
token buffering, HITL decision shapes, the channel filter, and that queued turns don't
overlap.

`build_app()` starts nothing — `serve()` creates the worker. That keeps the wiring
testable: a test can deliver an event and assert it was queued without a turn firing.

They **cannot** tell you whether your tokens are valid, your scopes are right, or the
bot was invited to the channel. All three fail *silently*. That needs a live run —
checklist in `docs/loop3_slack.md`.