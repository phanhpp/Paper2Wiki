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

Supervisor receives all structured summaries and:

- **≤2 traces** → present 1 concise summary, no clustering needed
- **>2 traces** → identify patterns and issues, group by categories (e.g. `error_type`, `skill_compliance`), rank by frequency + severity

**Before presenting the report, validate each finding against git history:**

```bash
git --no-pager log --oneline -n 20 -- <affected_file>
git --no-pager diff HEAD~5 -- <affected_file>
```

- If recently modified → issue may already be fixed, strike it from the report with a note
- If unchanged since traces were recorded → finding still valid, include as normal
- If git history is inconclusive → ask the user whether the issue has already been addressed before proposing a fix

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

### 4. Commit & Log

**Always log after presenting the report** — even if there are no findings or no changes to commit.

1. Append to `trace_analysis_log.md` (create if it doesn't exist):

```markdown
## 2026-05-01T14:30:00Z
- Project: my-agent
- Traces analyzed: 42 (last 7 days)
- Key findings: sha256_mismatch (6), hitl_rejected (3)
- Changes committed: updated AGENTS.md Known Pitfalls
```

2. If a `traces_path` file was used, delete it:

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