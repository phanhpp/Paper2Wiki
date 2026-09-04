---
name: trace-analysis
description: Use this skill when the user asks to run trace analysis, identify anomalies or issues in agent behaviour patterns, evaluate skill or tool usage, or self-evaluate and improve. Triggers include phrases like "analyze traces", "check agent behaviour", "find issues in recent runs", "what went wrong", "self-evaluate", or "improve based on traces".
---

# Trace Analysis Skill

Analyzes LangSmith traces to identify behavioural patterns, anomalies, skill deviations, and tool misuse — then surfaces ranked findings with actionable recommendations.

## When this skill activates

Use this skill when the user:

- Asks to run trace analysis or identify anomalies in agent behaviour patterns
- Asks to identify issues with any skills or tool usage
- Asks to self-evaluate or improve based on recent runs

---

## Flow

### 1. Fetch

**Before calling `run_trace_report_async`, determine the `error` argument from the user's phrasing:**
- "analyze errors", "traces with errors", "what failed", "failures" → **`error=True`**
- "analyze successful runs", "what worked" → `error=False`
- "analyze traces", "self-improve", "check behaviour" (no qualifier) → omit `error`

Call `run_trace_report_async` tool to fetch recent traces from LangSmith.

| Arg | Description |
| --- | --- |
| `project` | Project name |
| `days` | Lookback window in days (`start_time = now - days`) |
| `limit` | Maximum number of runs to fetch |
| `error` | `True` → only traces with errors; `False` → only successful traces; omit for all |

Offloading to a JSON file is handled automatically by the tool. If `traces_path` is present in the returned report, read traces from that file; delete it after logging in step 4.

Use default arguments unless the tool returns no runs → increase `days` by 1 or 2.

#### structure

- Runs are grouped by **trace** (one trace = one user request, spanning all its sub-operations).
- Within each trace, runs are printed in execution order with **depth** indicating nesting:
  - `depth=0` — root (the top-level chain triggered by user input)
  - `depth=1` — direct child (e.g. an llm call or tool call spawned by the root)
  - `depth=2+` — nested further

#### Reading LLM runs

Each run has a `run_type`. For `llm` runs, inputs and outputs are expanded only for **errored calls** and the **last call** in the trace — all other LLM calls show one line only (redundant prefixes).

- **Inputs**: non-system messages with `role` + compact `kwargs`. Tool outputs for verbose tools (`read_file`, `execute`, etc.) are redacted as `[redacted — N chars]` — focus on tool *choice* and *order*, not content.
- **Outputs**: denoised LangChain structure — read `message.kwargs.content` (text/tool_use blocks) and `message.kwargs.tool_calls` to understand what the model decided to do.

### 2. Summarize

Call `summarize_traces_async(report)` once.

The tool now handles batching internally:

- Splits traces into pages of `limit=50`
- Fires all pages in parallel (`offset=0, 50, 100, ...`)
- Merges and returns one combined summary list

If one page fails and you need a targeted retry, call:
`summarize_traces_async(report, offset=<failed_offset>, limit=50)`

- **Do not raise `limit` above 50** — larger pages hit Haiku's context limit
- **Never re-fetch traces if summarization fails** — retry only the failed page via `offset`/`limit`

### 3. Cluster & Synthesize

upon receives all structured summaries if:

- **≤2 traces** → present 1 concise summary, no clustering needed
- **>2 traces** → identify patterns and issues, group by categories (e.g. `error_type`, `skill_compliance`), rank by frequency + severity

Take the summaries as they are, **do not fall back to reading the raw traces**

**Before presenting the report, validate each finding against git history:**

```bash
git --no-pager log --oneline -n 20 -- <affected_file>
git --no-pager diff HEAD~5 -- <affected_file>
```

- If recently modified → issue may already be fixed, strike it from the report with a note
- If unchanged since traces were recorded → finding still valid, include as normal
- If git history is inconclusive → ask the user whether the issue has already been addressed before proposing a fix

**Run anomaly detection** — call `detect_anomalies_async(report)` on the same report used in step 1. This produces a structured `AnomalyReport` with `hard_error`, `latency_spike`, `token_blowout`, and `step_count_spike` signals. Merge these findings into the report below — anomaly signals are ground truth, cluster observations are qualitative context.

**Always ask the user to confirm findings before proposing any fixes.** Present the report and wait for acknowledgement — do not proceed to Step 4 until the user explicitly approves.

Then present the validated report:

```markdown
## Trace Analysis Report — last 7 days (N sessions)

### Findings (ranked)

1. skill_deviation:sha256_mismatch (6 traces, 9%) — agent computing sha256 on raw body
   including leading newline, causing hash mismatch against stored value
2. hitl_rejected (3 traces, 4%) — user rejected proposed AGENTS.md change,
   agent did not explain reasoning before presenting diff

~~3. sandbox_timeout~~ — resolved in commit a3f92c1 (2 days ago), no action needed

### Proposed Changes

**AGENTS.md — add to Known Pitfalls:**
\```
## Known Pitfalls

- sha256 re-computation needs `body.lstrip('\n')` to match the stored value:
  `hashlib.sha256(body.lstrip('\n').encode('utf-8')).hexdigest()`
  where body = everything after the closing --- delimiter
\```

Approve changes? (HITL required before writing)
```

### 4. Push to Datasets

Call `create_datasets_from_anomaly_report(anomaly_report, eval_cases=True)` to push failing spans to LangSmith datasets and generate PR gate candidates.

The tool returns:
```json
{
  "datasets": {"<dataset_name>": {"new": N, "total": N}},
  "suggested_cases": [
    {
      "id": "regression_fetch_arxiv_a3f92c1",
      "type": "regression",
      "category": "retrieval",
      "tool": "fetch_arxiv",
      "inputs": {"query": "1706.03762"},
      "_review": "fill in expect_keys or expect_error after fix is merged"
    }
  ]
}
```

- Each anomaly cluster becomes one LangSmith dataset, scoped by flow + run_type + run_name
- Idempotent — already-pushed run_ids are skipped automatically
- `suggested_cases` contains candidates for `run_type == "tool"` + `hard_error` only — tool crashes are deterministic: fix it, prove it stays fixed. 

#### `eval/pr_gate_cases.json` field schema

Each case in `eval/pr_gate_cases.json` has these fields:

| Field | Required | Description |
|---|---|---|
| `id` | ✅ | Unique snake_case string. Convention: `regression_<tool>_<short_hash>` for auto-generated cases |
| `type` | ✅ | `"regression"` — blocks merge if it drops below threshold. `"capability"` — tracked only, never gate-blocking |
| `category` | ✅ | Groups cases for scoring: `"retrieval"`, `"health"`, `"boundary"` |
| `tool` | ✅ | Exact tool name as registered (matches `t.name` in `all_tools`) |
| `inputs` | ✅ | Dict passed to `tool.ainvoke(inputs)` — must be JSON-serializable |
| `expect_keys` | one of | List of strings that must appear in the JSON-serialized output. Use when the tool should succeed and return known fields |
| `expect_error` | one of | `true` — test passes if the tool raises any exception. Use for invalid inputs that should always error gracefully |
| `expect_error_contains` | one of | String that must appear in the exception message (case-insensitive). Use for boundary/SSRF checks |
| `expect_empty` | one of | `true` — test passes if the result is falsy/empty. Use for empty-input edge cases |
| `_review` | ❌ | Human note — strip before merging |

At least one `expect_*` field is required for `run_gate.py` to assert anything meaningful. A case with no `expect_*` only checks the tool doesn't crash — valid but weak.

#### Completing auto-generated cases (HITL)

Auto-generated cases have `_review` set and no `expect_*` field. Before presenting to the user, **use the anomaly signal and tool name to suggest the missing assertion:**

- Read `span.signals` for the error message. If it contains an HTTP error, invalid ID, malformed input → the input was invalid → suggest `"expect_error": true`
- If the error looks like a bug on valid input (connection error, parsing crash, unexpected None) → suggest `"expect_keys": [<known output fields for this tool>]` — check the tool's return type or other passing cases in `eval/pr_gate_cases.json` for reference
- Strip `_review` from the final case before writing

Known output fields per tool (for `expect_keys` suggestions):

| Tool | Typical output keys |
|---|---|
| `fetch_arxiv` | `title`, `pdf_path`, `metadata` |
| `parse_pdf_docling` | `content`, `page_count` |
| `web_search` | `title`, `url` |
| `web_extract` | `content`, `url` |
| `quick_wiki_integrity_check` | `pages_checked` |

**HITL presentation format** — present one case at a time if multiple, or all together if ≤3:

```
## Suggested eval/pr_gate_cases.json entries (N cases from tool hard errors)

For each case, I've inferred the missing assertion from the error signal:

1. `regression_fetch_arxiv_a3f92c1`
   Tool: fetch_arxiv | Inputs: {"query": "INVALID999"}
   Error signal: "No paper found for ID INVALID999"
   → Input looks invalid — suggesting `expect_error: true`

   Final case:
   {"id": "regression_fetch_arxiv_a3f92c1", "type": "regression", "category": "retrieval",
    "tool": "fetch_arxiv", "inputs": {"query": "INVALID999"}, "expect_error": true}

Approve writing these to eval/pr_gate_cases.json? (edit any case before confirming)
```

After approval, read `eval/pr_gate_cases.json`, append approved cases (with `_review` stripped), and write back. HITL fires automatically at `write_file`.

Skip this step entirely if there are no anomalies in the report (pure skill_deviation findings with no failed spans).

### 5. Commit, PR & Log

**Always log after presenting the report** — even if there are no findings or no changes to commit.

1. Append to `trace_analysis_log.md` (create if it doesn't exist):

```markdown
## 2026-05-01T14:30:00Z
- Project: my-agent
- Traces analyzed: 42 (last 7 days)
- Key findings: sha256_mismatch (6), hitl_rejected (3)
- Changes committed: updated AGENTS.md Known Pitfalls
```

2. If changes were committed, open a PR for user review:

```bash
gh pr create \
  --title "trace-analysis: <finding summary>" \
  --body "$(cat <<'EOF'
## Findings
<paste ranked findings from report>

## Changes
<list every file modified and why>

## Datasets created
<list dataset names pushed in step 4, or "none">

🤖 Generated by trace-analysis skill
EOF
)"
```

Skip PR creation if no files were changed (e.g. findings-only report with no proposed fix).

3. If a `traces_path` file was used, delete it:

```bash
rm <traces_path>
```

Do not delete earlier — the file serves as a fallback if the cycle is abandoned before logging completes.

---

## HITL Checkpoints

HITL is enforced automatically at the infrastructure level via `interrupt_on` for:

- `execute` — all shell/git commands
- `write_file` — all file writes
- `edit_file` — all file edits

**Sensitive operations** — always show the user a clear summary of **what** and **why** before the interrupt fires:

- Writing to `/skills/`
- Writing to `/memories/AGENTS.md`
- `git commit`, `git push`

---

## Git Commands

**Run every command from the repo root, with relative paths.** The shell is already there.
Never use `git -C <path>` or `cd` — the virtual `/`-rooted paths your file tools show do
not exist in the shell, so `git -C /llm_wiki …` fails with
`fatal: cannot change to '/llm_wiki'`.

**A fresh branch has no upstream**, so the first push needs
`git push -u origin <branch>`; a bare `git push` fails.

Always use `--no-pager` and `-n <limit>` to avoid pager hanging.

**Read-only (safe):**

```bash
git --no-pager log --oneline -n 20
git --no-pager log --stat -n 10
git --no-pager diff
git --no-pager diff <commit-a> <commit-b>
git --no-pager show <commit-id>
git --no-pager blame <file>
git --no-pager log -- <file>
git status
```

**Write (always justify to user):**

```bash
git add <file>
git commit -m "..."
git push
git stash
git stash pop
git checkout -b <new-branch>
```

**Never use:**

```bash
git reset --hard    # permanent data loss
git rebase          # rewrites history
git push --force    # overwrites remote
git clean -fd       # deletes untracked files
git checkout <file> # silently discards local changes
```

---

## Watermark

Watermark file: `trace_analysis_log.md`

Check for existence with `ls` before creating. Add `trace_analysis_log.md` to `.gitignore`.

Used to watermark completed analysis cycles so the agent avoids re-analyzing the same traces on the next run. Written in step 4 after changes are committed.

## Debugging Heuristics

Not all errors indicate real failures:

- **`StructuredTool does not support sync invocation`** — not a tool bug; agent was called with `agent.stream` instead of `agent.astream`
- **`GeneratorExit` / `KeyboardInterrupt`** — user cancelled, not an error.