# `src/connectors/` — phase 1: deterministic fetch

Ingest is two phases, deliberately separated:

```
phase 1   connectors/   dumb, deterministic, NO LLM
          hit the source → dump the raw response → record it → exit

phase 2   the agent     read the dumps → write and maintain wiki pages
```

The point of the split: if the model writes a bad page, **re-synthesis is free** —
the raw data is already on disk, so nothing is re-fetched. Same rule `wiki/raw/`
already follows.

Run it: `any2wiki fetch git-repo` (or `fetch` for everything configured).

## The files

| File | What it does |
|---|---|
| `base.py` | The `Item`/`Connector` contract, and **all** disk, hashing and ledger work |
| `http.py` | Resilient HTTP — timeout, bounded retry, backoff with jitter |
| `git_repo.py` | A local git repository as a source |
| `__init__.py` | `REGISTRY` — add a connector with one line |

## What a connector may do — and may not

A connector implements **one method**:

```python
class MyConnector:
    name = "my-source"
    def fetch(self, config, cursor) -> Iterator[Item]:
        yield Item(id=..., source_url=..., payload=..., cursor=...)
```

**It never writes files.** `base.py` owns every write, derives every hash, and
updates the ledger. That is not a style preference — it is what makes the rules
below impossible to violate. A connector cannot forget to record a content hash,
because it never writes one.

*Why that matters:* OpenWiki wrote the same rule into a skill file
(`write-connector/SKILL.md`: "store IDs, last edited times…") and their own
connectors don't follow it, because nothing enforced it. A rule in a document is
documentation; a rule in a type signature is enforcement.

## The ledger — one entry per item

`connectors/<name>/manifest.json`:

```jsonc
{ "connector": "git-repo", "cursor": "46b865d…", "last_run": "…",
  "items": {
    "any2wiki": {
      "path": "raw/any2wiki.json",
      "source_url": "file:///…",
      "content_hash": "sha256:…",   // unchanged → skip the rewrite
      "fetched_at": "…",
      "deleted_at": null,            // it stopped appearing at the source
      "synthesised_at": null         // not yet turned into a wiki page
    } } }
```

Each field buys one capability:

| Field | Enables | Without it |
|---|---|---|
| `content_hash` | dedupe | re-dumping identical data every run |
| `deleted_at` | deletion detection | a removed item's page lives on forever |
| `cursor` | incremental fetch | re-reading the whole source each time |
| `synthesised_at` | the phase-1 → phase-2 boundary | synthesis cost grows with the archive |

**This is per *item*. OpenWiki's connectors record per *run***, which is the root
cause of everything their fetch side can't do. Ours is the one place we
deliberately diverge from them — and it's therefore the one place with no
production track record, so treat surprises here as ours, not theirs.

Two guarantees from `base.py`:

- **Written after every item**, not at the end — a crash mid-fetch keeps whatever
  completed.
- **Deletion is only inferred on a full sweep.** A cursor-based run sees a window,
  so an item outside it is not gone.

## Failures are isolated per item

One bad item becomes a warning; the rest of the fetch continues and what
succeeded is kept.

OpenWiki's Gmail connector loops without a `try`, so one 403 on message 6 of 20
throws away messages 1–5 too; their X connector gets it right. Putting the
isolation in the shared layer means no connector here can repeat that.

## `http.py`

30s timeout · 3 retries · exponential backoff with **full jitter**, capped at 20s ·
honours `Retry-After` *within* that cap (so "retry in an hour" can't stall a run) ·
retries 429/5xx and network errors.

**401 and 403 are returned unretried, on purpose** — a caller needs to *see* a 401
to trigger a token refresh. Retrying would hide it and can lock accounts.

`sleep` and `rand` are injectable, purely so the retry paths are testable without
real delays or real jitter.

## `git-repo`

Yields one item per configured repo: branch, head, recent commits, changed files,
file tree, and a **working-tree fingerprint**.

The fingerprint hashes `HEAD` *plus the content of every tracked and untracked
file*. Comparing `HEAD` alone is wrong — uncommitted edits don't move it, so a
page whose source you just edited would look current. Git's own ignore rules
apply, so `.venv` churn doesn't make every run look dirty.

**The stored head is unusable in three cases**, all falling back to a working-tree
diff rather than erroring: first run, nothing new committed, or the head no
longer resolves (force-push, gc). The check is
`rev-parse --verify --quiet <sha>^{commit}` — the `^{commit}` matters, it asserts
the object is a commit rather than merely existing.

**Known limit:** the cursor is one value per connector, so multiple repos share it.
Fine for a single repo; multi-repo incremental needs per-repo cursors.

## Tests

`tests/connectors/` — 41 unit tests, no network. `git_repo` runs against real
temporary repositories (`git init` in a fixture) rather than mocks: git is local
and deterministic, so mocking it would only test the mock.
