# `src/middleware` — in-run wiki verification

**Checks the agent's wiki work before letting a run finish, and sends it back to fix mistakes.**

`quick_wiki_integrity_check` is a *tool*, so the model can skip it or ignore what it says. A check
the model can decline is a suggestion, not verification. This moves the decision out of the model's
hands.

**No LLM call anywhere in the loop.** Every check reads files and compares strings, so it costs
nothing. Judging *quality* — is the page faithful to the source paper? — stays in
`eval/run_weekly_eval.py`, where the whole paper can go in the prompt.

## The flow

One pass per turn:

```
before_agent      list every file in wiki/ and marp-slides/
                  note what the user asked
      │
 (agent works)    awrap_tool_call notes each tool used and each file read
      │
after_agent       list the files again and compare  →  what this run wrote
                  │
                  ├─ wrote entities/ or concepts/  →  ingest checks (S1–S9)
                  ├─ wrote marp-slides/            →  marp checks   (M1–M4)
                  ├─ user asked to use the wiki    →  query checks  (Q1–Q3)
                  └─ none of those                 →  stop, say nothing
                  │
                  ├─ all checks pass  →  stop
                  └─ something wrong  →  send the agent back with the list of
                                         problems, up to max_iterations times,
                                         then report and stop
```

A run can match more than one path — "ingest this paper and make slides" is both, and all matching
checks run.

## Where things live


| File                 | What it does                                                                                                                                          | Go here when                                                    |
| -------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `classify.py`        | Decides whether this run was an **ingest**, a **query**, a **marp** deck, or none — from which files it wrote and whether the user asked for the wiki | a run gets checked when it shouldn't, or doesn't when it should |
| `checks/ingest.py`   | The 9 ingest checks (below)                                                                                                                           | changing what counts as a correct ingest                        |
| `checks/query.py`    | The 3 query checks (below)                                                                                                                            | same, for questions answered from the wiki                      |
| `checks/marp.py`     | The 4 slide checks (below)                                                                                                                            | same, for slide decks                                           |
| `checks/common.py`   | Reads wiki files for the checks — `index.md`, `log.md`, `graph.json`, frontmatter, wikilinks                                                          | a check needs to look something up                              |
| `checks/__init__.py` | Which checks belong to which path                                                                                                                     | adding a check or a path                                        |
| `feedback.py`        | Writes the message sent back to the agent when checks fail                                                                                            | the agent doesn't understand what to fix                        |
| `types.py`           | The shared shapes: what a check receives (`RunContext`), returns (`CheckResult`), and what the caller is told (`Evaluation`)                          | you need to know what a check can see                           |
| `wiki_rubric.py`     | The middleware itself — snapshots the wiki, runs the checks, sends the agent back to retry                                                            | changing retry behaviour, state, or hooks                       |


**Reading order if you're new:** `classify.py` → `checks/ingest.py` → `wiki_rubric.py`. The first two
are plain functions and cover most of the behaviour; the third is plumbing.

**Only** `wiki_rubric.py` **imports LangChain.** Everything else is plain Python over a `RunContext`,
which is why the checks test without running an agent.

## How a run is classified

Done by `classify.py`, from the files the run wrote and the user's message.


| Path     | Triggered when the run…                                                                            |
| -------- | -------------------------------------------------------------------------------------------------- |
| `ingest` | wrote a file under `wiki/raw/`, `wiki/entities/` or `wiki/concepts/`                               |
| `query`  | the user's message asked for the wiki ("use the wiki", "what do we know about", "check our notes") |
| `marp`   | wrote a file under `marp-slides/`, or used the `marp-slide-creator` subagent                       |
| *(none)* | none of the above — ungraded and silent, which is every ordinary chat message                      |


**Tool names never trigger** `ingest`**.** A run that called `fetch_arxiv` and wrote nothing is usually
correct — the paper was already ingested. Triggering on tools would fail every deliberate re-ingest.

## How "what did this run write?" is answered



### What gets watched

`RunContext` is the bundle of everything a check may look at for one run — the files written, the
files read, the tools used, the question and the answer.

Two directories feed it, because `marp-slides/` is a **sibling** of `wiki/` — listing `wiki/` never
reaches it:

```
llm_wiki/
├── wiki/           ← wiki_snapshot      → RunContext.writes  (paths written under wiki/)
└── marp-slides/    ← artifact_snapshot  → RunContext.artifacts  (files written under marp/)
```

Slides land in `marp-slides/` because the marp subagent builds them in the Daytona sandbox and
downloads them to the host. Watching only `wiki/` meant a deck produced zero detected writes, so the
marp path never fired and decks went unchecked.

Each of those two names is a **snapshot**: a listing of every file in that directory, with its
modified time and size.

```python
wiki_snapshot = {
    "index.md":                   "1754...:412",   # mtime : size in bytes
    "log.md":                     "1754...:88",
    "concepts/self-attention.md": "1754...:901",
}
```



### How a write is spotted

List the files before the run, list them again after, compare.

**Before the agent starts**, `before_agent` takes both snapshots and stores them in state:

```
index.md                    412 bytes, 10:00:00
log.md                       88 bytes, 10:00:00
concepts/self-attention.md  901 bytes, 10:00:00
```

**The agent runs.** It writes whatever it writes.

**After it finishes**, `after_agent` lists everything again and compares against the stored snapshot:

```
index.md                    480 bytes, 10:00:31   ← changed
log.md                      142 bytes, 10:00:33   ← changed
concepts/self-attention.md  901 bytes, 10:00:00   ← same
concepts/attention.md       760 bytes, 10:00:29   ← new
```

`index.md`, `log.md` and `concepts/attention.md` are this run's writes. They become
`RunContext.writes`, which the checks read.

### The functions

Both live in `wiki_rubric.py`.


| Function                 | Argument                                                                    | Returns                                                     |
| ------------------------ | --------------------------------------------------------------------------- | ----------------------------------------------------------- |
| `snapshot_dir(root)`     | `root` — the directory to list the files written(`wiki/` or `marp-slides/`) | `{path: "mtime:size"}` for every file under it              |
| `diff_dir(before, root)` | `before` — a snapshot taken earlier; `root` — the same directory            | the paths whose entry differs, or that weren't there before |


Each is called **once per watched directory** — once for `wiki/`, once for `marp-slides/`:

```python
# before_agent
wiki_snapshot     = snapshot_dir(wiki_root)        # wiki/
artifact_snapshot = snapshot_dir(ARTIFACTS_DIR)    # marp-slides/

# after_agent
writes    = diff_dir(wiki_snapshot,     wiki_root)        # -> RunContext.writes
artifacts = diff_dir(artifact_snapshot, ARTIFACTS_DIR)    # -> RunContext.artifacts
```



### Two design notes

**Why size *and* modified time.** `log.md` is appended to, not replaced. Two appends in the same
second show the same timestamp and look untouched. The size still changes, so we catch it — which is
what makes check S5 ("was `log.md` appended this run?") reliable.

**Why not just watch the tool calls.** The agent writes files by more routes than `write_file` —
shell commands, the sandbox download tool, and whatever gets added later. None of those appear as a
`write_file` call. Reading the files on disk catches every write however it was made, and needs no
update when a new tool appears.

**Limit:** this assumes one run at a time per wiki. Fine for the CLI and for GitHub Actions (separate
processes); wrong for a long-lived daemon serving several threads at once, where each run would see
the others' writes as its own.

## The checks



### `ingest` — S1–S9


|     | Checks that…                                                                                       |
| --- | -------------------------------------------------------------------------------------------------- |
| S1  | a page was actually written under `entities/` or `concepts/` (not just a file fetched into `raw/`) |
| S2  | each new page has frontmatter with `title`, `created`, `updated`, `type`, `tags`, `sources`        |
| S3  | every `[[wikilink]]` on a new page points at a page that exists                                    |
| S4  | `index.md` lists each new page                                                                     |
| S5  | `log.md` was appended during this run                                                              |
| S6  | `graph/graph.json` has a node for each new page                                                    |
| S7  | that node has at least one edge — an isolated node adds nothing                                    |
| S8  | the node's `path` points at a file that exists                                                     |
| S9  | the page's `sources` exist on disk, so it's traceable back to what it came from                    |




### `query` — Q1–Q3


|     | Checks that…                                                                                                                                                                                                                                                                                             |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Q1  | the answer cites at least one `[[wikilink]]` and they all resolve. Required — the user asked for the wiki, so an uncited answer either ignored it or is claiming knowledge it can't attribute                                                                                                            |
| Q2  | the run actually saw the pages it cited. A citation to a page never opened is a fabricated attribution. Counts both `read_file` and search results — `grep`/`glob` return matching paths, and `grep` with `output_mode="content"` returns the lines themselves, so a page found that way really was seen |
| Q3  | *if* the answer was saved under `queries/`, that page has valid frontmatter, is in `index.md`, and was logged. Saving is optional — not saving is never a failure                                                                                                                                        |




### `marp` — M1–M4


|     | Checks that…                                                                                                                                                                     |
| --- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M1  | a `.md` deck was downloaded into `marp-slides/`. Decks are built in the Daytona sandbox; if the download is skipped the user is told slides exist while nothing reached the host |
| M2  | the deck declares `marp: true`. Without it Marp renders plain markdown — the file looks right and produces no slides                                                             |
| M3  | the deck has more than one slide (the `---` separators weren't omitted)                                                                                                          |
| M4  | *if* the user asked for a specific number of slides, the deck is within ±2 of it                                                                                                 |




### TODO — two semantic checks

Designed but not built. Both need an embedding index (`sqlite-vec`), which is why they were deferred:

| | Would check |
|---|---|
| Q4 | the pages the agent read are actually *relevant* to the question, not just cited |
| Q5 | the wiki held relevant pages that the agent ignored — it answered from model knowledge instead |

Q1–Q3 already catch the structural failures (no citation, a citation to a page never opened, a badly
saved answer). These two catch *relevance*, which string comparison cannot.

When building them: index `index.md` summaries only, not page content. Bias both thresholds toward
passing — a checker that cries wolf on normal use gets switched off. See
`src/middleware/__init__.py`.

## What the agent sees when a check fails

```
Your work did not pass verification. Fix the following, then finish the task.
Do not start over — correct what is listed.

- [S4] index.md has no entry for ['self-attention']
- [S6] graph.json has no node for ['self-attention']
```

If the checks failed *and* the run never read `skills/llm-wiki/SKILL.md`, that instruction leads —
fixing the symptom without reading the conventions tends to reproduce the same gap next attempt.

## Outcomes


| Verdict                  | Meaning                                    | Reported to `on_evaluation`?         |
| ------------------------ | ------------------------------------------ | ------------------------------------ |
| `no_path_matched`        | not a wiki run                             | no — it would fire on every "thanks" |
| `satisfied`              | everything passed                          | yes                                  |
| `needs_revision`         | something failed, retrying                 | yes                                  |
| `max_iterations_reached` | still failing after the retry cap          | yes                                  |
| `check_error`            | a check crashed — our bug, not the agent's | yes                                  |


`max_iterations_reached` does **not** inject a message or interrupt. The agent's final answer is
returned unchanged and the verdict reaches the caller through `on_evaluation` and the event stream,
so unattended runs are never blocked.

## Adding a check

1. Write a function in `checks/<path>.py`:

```python
def index_updated(ctx: RunContext) -> CheckResult:
    missing = [slug_of(r) for r in _pages(ctx) if not index_has(ctx.wiki_root, slug_of(r))]
    if not missing:
        return CheckResult.ok("S4")
    return CheckResult.fail("S4", f"index.md has no entry for {missing}")
```

1. Append it to that module's `CHECKS` list.
2. Add a test with a seeded defect in `tests/test_wiki_checks.py`.

Two rules worth keeping:

- `gap` **names the specific failure** (`graph.json has no node for 'attention'`), never restates the
rule. Targeted feedback is the only reason a retry beats a plain re-run.
- **Scope to what this run did.** A whole-wiki sweep fails every future run for pre-existing drift the
current run didn't cause — see `graph_consistent` (S8), deliberately limited to pages written this
run. Whole-wiki consistency is maintenance (`scripts/clean_wiki.py`), not an in-run gate.



## Config

```yaml
verification:
  enabled: true        # kill switch — false makes the middleware inert
  max_iterations: 2
```

Built by `WikiRubricMiddleware.from_config()` in `src/agents/agent.py:create_supervisor()`.

## Tests

```bash
uv run pytest tests/middleware -q
```

Three stages: helpers, checks, then whole paths driven through the real
middleware. See `TESTS.md` for the scenarios each stage covers, and for what
only a real run can tell you.