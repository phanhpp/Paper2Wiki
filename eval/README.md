# Eval architecture

## How it all connects

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  TRACK 1 — PR Gate  (every PR, no agent, no LLM, no secrets)               │
│                                                                             │
│  eval/pr_gate_cases.json                                                            │
│      │  tool inputs + assertions (regression / capability types)            │
│      ▼                                                                      │
│  eval/run_gate.py ──► tool.ainvoke() ──► eval/results.json                 │
│      │                (src/tools directly,                                  │
│      │                 no agent routing)                                    │
│      ▼                                                                      │
│  (capability cases: tracked + printed, never block — promote to            │
│   "regression" type once reliably passing to lock into the hard gate)       │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│  TRACK 2 — Weekly Golden Eval  (weekly schedule or path-conditional on PR)  │
│                                                                             │
│  eval/golden_datasets/{ingest,query,marp}.json                              │
│      │  cases versioned in git                                              │
│      ▼                                                                      │
│  eval/push_golden_datasets.py ──► LangSmith datasets                       │
│      --dataset {ingest,query,marp}    (idempotent, skips existing)          │
│      ▼                                                                      │
│  eval/run_weekly_eval.py                                                    │
│      │  _stream_agent()  ──► create_supervisor() ──► agent.astream()        │
│      │                       auto-approves HITL interrupts                  │
│      │  run_ingest / run_query / run_marp  (target functions)               │
│      │      ▼                                                               │
│      │  aevaluate(target, data=LangSmith dataset, evaluators=[...])         │
│      │      ▼                                                               │
│  eval/golden_evaluators.py                                                  │
│      code evals: no_crash, index_updated, log_updated, trajectory_subseq   │
│      LLM judges: wiki_faithfulness, answer_grounded, slide_quality, ...     │
│      (all call claude-haiku-4-5 via anthropic.Anthropic())                  │
│      ▼                                                                      │
│  LangSmith experiment results  (--no-gate during calibration phase)         │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│  TRACK 3 — Anomaly Detection Loop  (weekly CI + HITL only)                  │
│                                                                             │
│  eval/run_weekly_baselines.py                                               │
│      │  run_trace_report_async()  ──► LangSmith (fetch last N days)        │
│      │  compute_baselines_async() ──► memories/baselines.json              │
│      │                               {by_name: {medians}, by_flow: {steps}}│
│      ▼                                                                      │
│  [trace-analysis skill — HITL only, never automatic]                        │
│      │  detect_anomalies_async(report, baselines.json)                      │
│      │      flags: hard_error / latency_spike / token_blowout /             │
│      │             step_count_spike                                         │
│      │      skips: run_type==chain AND name==LangGraph (infra noise)        │
│      │  create_datasets_from_anomaly_report()                               │
│      │      ──► LangSmith anomaly datasets   (tracked; no interrupt fires)  │
│      │      ──► suggested pr_gate cases      (hard_error + run_type==tool)  │
│      ▼                                                                      │
│  approved cases appended to eval/pr_gate_cases.json  (HITL: write_file)     │
│      ──► from then on they run on EVERY PR via Track 1                      │
│      Promotion is the durable coverage — there is no separate weekly replay │
└─────────────────────────────────────────────────────────────────────────────┘

NOTE: memories/baselines.json  (Track 3 latency/token medians)
  is unrelated to Track 1 — Track 1 has no baseline file; capability gains are
  locked in by promoting a case to "regression" type, not by a stored score.
```

---

Note:

Regression tests measure performance consistency across application versions over time. They ensure new versions do not degrade performance on cases the current version handles correctly, and ideally demonstrate improvements over the baseline.

## How CI and eval fit together (what runs when, and what's manual)


| Trigger                                 | Runs                                                                                           | Automatic?               |
| --------------------------------------- | ---------------------------------------------------------------------------------------------- | ------------------------ |
| **Every PR / push**                     | `pytest -m unit` + `eval/run_gate.py` (Track 1)                                                | ✅ fully auto, no secrets |
| **PR touching ingest/query/marp paths** | the matching golden eval (Track 2)                                                             | ✅ auto, path-conditional |
| **Weekly schedule**                     | `run_weekly_baselines.py` — refresh `memories/baselines.json` only                             | ✅ auto                   |
| **On-demand: "Analyze my traces"**      | `trace-analysis` skill → detect anomalies → **create datasets + draft pr_gate cases**          | ❌ **HITL**               |


**The split that trips people up — two speeds:**

- **Automatic (CI) is *consume-only*.** Track 1 runs existing `pr_gate_cases.json` against a frozen
floor; Track 3 weekly refreshes `memories/baselines.json`. Neither generates anything new.
- **The *generative* step needs a human.** `create_datasets_from_anomaly_report` and the new
`pr_gate_cases.json` entries only happen when someone runs the `trace-analysis` skill. CI never
populates datasets or writes gate cases.

**Precisely which part is enforced** (`agent.py:208` — `interrupt_on` covers `execute`,
`write_file`, `edit_file`):

| Step | Interrupt? |
|---|---|
| Push failed spans → LangSmith datasets | **no** — the skill asks by convention; nothing enforces it |
| Append approved cases → `pr_gate_cases.json` | **yes** — it is a `write_file` |

So the datasets are a *superset* of what reaches the gate.

**Why there is no weekly replay.** There used to be a `pytest -m langsmith` job replaying
hard_error examples from those datasets. It was removed as redundant: a hard error becomes durable
coverage by being **promoted into `pr_gate_cases.json`**, which then runs on *every* PR — strictly
more often than weekly. The only thing lost is the `recovery_quality` LLM judge ("did it fail
*gracefully*"), which a deterministic gate can't express; that was judged not worth a second
harness. The loop closes when the fix and its gate case land in the same PR.

`create_datasets_from_anomaly_report` callers — **only** the `trace-analysis` skill (it's a `@tool`
exposed to the supervisor; never called by any CI script). See
`src/tools/observability_eval_tools/README.md` "Who calls what".

---

## Track 1 gate logic — what blocks a PR (read this before touching thresholds)

`run_gate.py` runs `pr_gate_cases.json`. **Only regression categories block the gate** — each must
score 100% (`REGRESSION_THRESHOLDS`). One regression case failing → <100% → merge blocked.

`**capability` cases are tracked but never block.** They're scored + printed (and shown on the PR),
but a failure/flake can't block merge. This makes them a safe **staging area** for things that work
but aren't reliable yet (network-flaky web/arXiv calls, aspirational behaviors).

`**results.json` is OUTPUT, not input.** `run_gate.py` reads only `pr_gate_cases.json`, and *writes*
`results.json` fresh every run (local and CI). CI does **not** commit it; the repo copy is just the
last local snapshot and is never read back.

### Locking in a gain = promotion (not a baseline file)

When a capability case is **reliably passing**, promote it: change its `"type"` from `capability` →
`regression` in `pr_gate_cases.json`. Now it's in the hard 100% gate and can't regress. This
discrete promotion *is* the ratchet — there is **no** `capability_baseline.json` (a continuous
minimum-score file was redundant with promotion and was removed).

Going the other way: a deterministic regression case turning flaky? Demote it `regression` →
`capability` rather than lowering a threshold.

### Signal: when to promote

`run_gate.py` tells you — when a capability category scores 100%, every run prints:

```
📈 capability/retrieval = 100% — promote its cases to regression once reliably passing
```

- **Locally:** in the `run_gate.py` stdout.
- **On a PR:** the capability scores + nudge are written to the **GitHub checks summary** (the
rendered box on the PR page) via `$GITHUB_STEP_SUMMARY` — no need to open the job log.

---

## File map


| File                               | Track | Purpose                                                                                 |
| ---------------------------------- | ----- | --------------------------------------------------------------------------------------- |
| `eval/pr_gate_cases.json`          | 1     | Deterministic tool test cases — regression + capability                                 |
| `eval/results.json`                | 1     | **Output** of `run_gate.py`, rewritten every run. Not committed by CI, never read back. |
| `eval/run_gate.py`                 | 1     | Runs pr_gate_cases.json, writes results.json, exits 1 on regression drop                |
| `eval/golden_datasets/ingest.json` | 2     | 4 cases: 1 already-ingested, 1 full-ingest, 1 partial-ingest, 1 negative                |
| `eval/golden_datasets/query.json`  | 2     | 3 cases: 2 positive, 1 negative                                                         |
| `eval/golden_datasets/marp.json`   | 2     | 3 cases: all positive (tech/business/web-search)                                        |
| `eval/push_golden_datasets.py`     | 2     | Syncs JSON → LangSmith (`--dataset {ingest,query,marp}`)                                |
| `eval/golden_evaluators.py`        | 2     | Code evals + LLM judges for all 3 datasets                                              |
| `eval/run_weekly_eval.py`          | 2     | Target fns + aevaluate wiring + gate logic                                              |
| `eval/run_weekly_baselines.py`     | 3     | Fetch traces → compute `memories/baselines.json`                                        |
| `memories/baselines.json`          | 3     | Per-run-name latency/token medians (3× threshold for anomaly detection)                 |


---

## Which tools belong in the gate (design principle)

- **Blocking gate (regression) = deterministic, no-network, fast logic only** — so it never flakes and a 100% floor is trustworthy. 
- **Capability = network/external behavior** — tracked, non-blocking.
- **Complex-object / LLM tools belong in unit tests, not the JSON gate.**


| Tool                                                            | In gate? | As                         | Why                                                                 |
| --------------------------------------------------------------- | -------- | -------------------------- | ------------------------------------------------------------------- |
| `compute_sha256`                                                | ✅        | regression (hashing)       | pure function; guards the wiki `lstrip('\n')` raw-source convention |
| `web_extract` guards (SSRF/empty)                               | ✅        | regression (boundary)      | your guard logic, deterministic, no network                         |
| `quick_wiki_integrity_check`                                    | ✅        | regression (health)        | deterministic on the committed wiki                                 |
| `fetch_arxiv` / `web_search` / `web_extract` happy-path         | ✅        | **capability** (retrieval) | network/flaky → tracked, must not block merge                       |
| `detect_anomalies_async`, `compute_baselines_async`             | ❌        | unit tests                 | take `TraceReport` objects (not JSON) — already covered             |
| `summarize_traces_async`                                        | ❌        | unit tests                 | it's an LLM call                                                    |
| `run_trace_report_async`, `create_datasets_from_anomaly_report` | ❌        | unit tests                 | LangSmith network + secrets                                         |


### Current gate cases (what each one checks)

**Regression (blocking, deterministic, key-free):**

| Case | Checks |
|---|---|
| `sha256_basic` | exact SHA-256 of `"hello world"` — the hashing logic is correct |
| `sha256_strips_leading_newlines` | `"\n\nhello world"` hashes the **same** as `"hello world"` — confirms `lstrip('\n')` is applied (the wiki `raw/` convention: the body after `---` is hashed with leading newlines stripped, so the digest is stable regardless of blank lines) |
| `web_extract_empty_urls` | empty URL list returns `[]` immediately, before any provider call |
| `web_extract_localhost_blocked` | request to `localhost` is refused |
| `web_extract_private_ip_blocked` | request to a private range (`192.168.x`) is refused |
| `web_extract_metadata_ip_blocked` | request to `169.254.169.254` is refused |
| `wiki_check_runs` | wiki integrity check runs on the committed wiki and returns OK |

> **SSRF (Server-Side Request Forgery)** = tricking the server into making requests to addresses it
> shouldn't — e.g. internal services (`localhost`, private IPs) or the **cloud metadata endpoint**
> `169.254.169.254`, which on AWS/GCP returns instance credentials. `web_extract` ingests
> attacker-influenced URLs (from papers/web pages), so it must **block** these before fetching. The
> four `web_extract_*_blocked` cases are the regression guards that prove it does.

**Capability (tracked, non-blocking — network/external):**

| Case | Checks | Needs key |
|---|---|---|
| `fetch_arxiv_valid_id` | arXiv returns a paper (or a graceful `rate_limited`) | no (arXiv direct) |
| `web_search_returns_results` | a search returns `title`/`url` results | yes |
| `web_extract_arxiv_html` | extracting an arXiv HTML page returns `content` | yes |

### Candidate additions (deterministic, fast — extend the *blocking* gate)

- `wiki_check_detects_broken_link` (health) — fixture wiki with a known broken `[[link]]` → asserts
detection. Needs a tiny fixture but is fully deterministic + key-free.
- More `web_extract` SSRF vectors (boundary) — e.g. IPv6 `[::1]`, `0.0.0.0` — one-liners, key-free.
- A `compute_sha256` case with `lstrip_newlines: false` (hashing) — pins the non-stripped path.

Keep adding deterministic cases to **regression**; only network/external behaviors go to capability.

## Golden eval status


| Step | Item                                             | Status             |
| ---- | ------------------------------------------------ | ------------------ |
| 1    | `ingest.json` — 4 cases                          | ✅ done             |
| 2    | `query.json` — 3 cases                           | ✅ done             |
| 3    | `marp.json` — 2 cases                            | ✅ done             |
| 4    | `push_golden_datasets.py`                        | ✅ done             |
| 5    | `golden_evaluators.py` — code evals + LLM judges | ✅ done             |
| 6    | `run_weekly_eval.py` — target fns + aevaluate    | ✅ done             |
| 7    | Wire into weekly CI (path-conditional jobs)      | ✅ done             |
| 8    | Judge calibration — 20 hand-labeled examples     | ❌ manual           |
| 9    | Remove `--no-gate` once judge agreement > 80%    | ❌ post-calibration |


## Gate thresholds (active after calibration)


| Dataset | Threshold | Hard gate keys                             |
| ------- | --------- | ------------------------------------------ |
| ingest  | 80%       | `no_crash`, `index_updated`, `log_updated` |
| query   | 75%       | `no_crash`, `query_is_read_only`           |
| marp    | 67%       | `has_marp_frontmatter`, `file_saved`       |


