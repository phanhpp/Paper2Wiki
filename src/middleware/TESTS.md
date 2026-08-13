# Testing the verification middleware

Three stages, each catching a class of bug the others cannot.

```bash
uv run pytest tests/middleware -q          # all three stages, ~1s, no network
```

| Stage | File | Catches |
|---|---|---|
| 1. Helpers | `test_checks_common.py` | wiki files parsed wrongly |
| 2. Checks | `test_checks_ingest.py`, `test_checks_query_marp.py` | a check that misses its defect, or fails a good run |
| 3. Paths | `test_paths_end_to_end.py`, `test_middleware.py` | a path that never fires at all |

**Stage 3 is the one people skip, and it is where the worst bugs live.** A check
tested in isolation can be perfect while never running — the marp path once could
not trigger, because slides live outside `wiki/` and only the wiki was being
watched. No amount of check-level testing would have found that.

---

## Stage 1 — helpers (`test_checks_common.py`)

Plain functions over a temp wiki. Every helper in `checks/common.py` gets a
working case and a malformed one, because these must **degrade rather than
raise**: a raise becomes `check_error` (our bug) instead of a reported gap (the
agent's).

- slugs, wikilinks (aliases, anchors, links inside HTML comments)
- `index.md` entry parsing, both dash styles
- `graph.json`: valid, missing, truncated, not-a-dict, not-JSON
- frontmatter: present, absent, unterminated
- the check registry, including an unknown path returning `[]`

Verified by mutation, not just by passing: break each helper to `return None`
and confirm a test fails. All 12 caught. A test that still passes against a
broken implementation is testing nothing.

---

## Stage 2 — checks (`test_checks_ingest.py`, `test_checks_query_marp.py`)

Each check called directly with a hand-built `RunContext`. Every check needs
**both** directions:

- a clean run passes it
- one seeded defect fails it, and the gap message names the specific thing

| Check | Seeded defect |
|---|---|
| S1 | wrote only into `raw/`, no page |
| S2 | frontmatter missing `sources` |
| S3 | wikilink to a page that does not exist |
| S4 | page absent from `index.md` |
| S5 | `log.md` not in this run's writes |
| S6 | no graph node for the page |
| S7 | node exists but has no edges |
| S8 | node path points at a missing file |
| S9 | declared source not on disk |
| Q1 | answer cites nothing / cites a missing page |
| Q2 | cited a page never opened |
| Q3 | saved answer with no frontmatter and no index entry |
| M1 | only a non-`.md` artifact downloaded |
| M2 | deck missing `marp: true` |
| M3 | single-slide deck |
| M4 | 3 slides when 12 were asked for |

Two cases here are about **not** failing, which matter more than they look:

- S8 ignores dangling nodes for pages this run never touched. A whole-wiki sweep
  would fail every future run for old drift, and a checker that cries wolf gets
  switched off.
- Q2 is silent when nothing was read at all — Q1 already reports that, and
  blaming one failure twice makes the feedback worse.

---

## Stage 3 — paths end to end (`test_paths_end_to_end.py`)

A real agent (fake model, real filesystem) driven through each path, so the
snapshot → classify → check → retry wiring actually runs.

`conftest.py` provides tools that behave like the real ones in the ways that
matter: `write_file`/`read_file` take `file_path`, and **`grep` returns matching
paths in its result**, not its arguments.

### ingest

| Scenario | Expected |
|---|---|
| every check satisfied | `satisfied`, no retry |
| page written, nothing else | `needs_revision` ×2 → `max_iterations_reached` |
| bad first attempt, corrected on retry | `needs_revision` → `satisfied` |
| re-ingest: nothing written because it already exists | no path, no callback |
| page written outside the tool layer | still detected |

### query

| Scenario | Expected |
|---|---|
| read a page, cited it | `satisfied` |
| answered from memory, cited nothing | Q1 fails |
| read one page, cited another | Q2 fails |
| found the page via `grep`, then cited it | `satisfied` |
| question that never asked for the wiki | no path, no callback |
| answer saved to `queries/` with no frontmatter or index entry | Q3 fails |

### marp

| Scenario | Expected |
|---|---|
| deck downloaded to `marp-slides/` | `satisfied` — proves the second snapshot works |
| claimed slides, downloaded nothing | no path (nothing to check) |
| deck missing `marp: true` | M2 fails |
| single-slide deck | M3 fails |

### combined and cross-cutting

| Scenario | Expected |
|---|---|
| "ingest this and make slides" | both S and M checks run |
| a check raises | `check_error`, run still returns an answer |
| plain chat | no callback — silence is the point |
| `enabled=False` | completely inert |
| `on_evaluation` raises | run survives |
| turn 2 after an ingest turn | turn 1's writes not re-graded |

---

## Two regression tests worth keeping

Both were live bugs, both invisible at stage 2:

**`test_marp_deck_detected_from_the_artifacts_dir`** — `marp-slides/` is a
sibling of `wiki/`, so watching only the wiki meant the marp path never fired and
decks went permanently unchecked.

**`test_query_page_found_by_grep_counts_as_seen`** — reads were once taken only
from `read_file`, so an agent that found a page with `grep` and cited it was
accused of fabricating the citation.

---

## What tests cannot tell you

Needs a real model and a real run:

1. **Does the agent understand the feedback and fix things?** The fake model
   returns canned responses; it never reads the message. This is the biggest
   unknown, and the whole design rests on it.
2. **HITL interaction.** `write_file` sits behind an interrupt. Whether
   `attempts` survives an interrupt-and-resume is untested — a checkpoint
   restore mid-retry could reset the counter and loop past the cap.
3. **Marp for real.** The checks have never seen a deck that Daytona actually
   built and downloaded.
4. **Snapshot cost.** Every turn walks the wiki twice. Nothing at 17 pages; worth
   measuring at a few thousand.
5. **The `wiki_rubric_evaluation` stream event.** Emitted, but the CLI renderer
   has never received one.

First manual run to try:

```bash
uv run python -m src.cli.app chat "ingest <arxiv-url>" --eval-mode
```

Then delete the new page's line from `index.md` and repeat the same ingest. You
should see it retry with a specific gap, and either fix it or report
`max_iterations_reached`. That single run exercises 1, 2 and 4 together.
