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

Call `run_trace_report_async` tool to fetch recent traces from LangSmith.

| Arg | Description |
| --- | --- |
| `project` | Project name |
| `days` | Lookback window in days (`start_time = now - days`) |
| `limit` | Maximum number of runs to fetch |
| `offload` | If `True`, write traces to JSON file instead of returning inline |

Use default arguments unless:

- Tool returns no runs → increase `days` by 1 or 2
- `limit > 100` → set `offload=True`

**If `offload=True`:** the traces will be accessed through `traces_path` instead of inline `traces`. Delete this file after logging in step 4.

#### Output structure

- Runs are grouped by **trace** (one trace = one user request, spanning all its sub-operations).
- Within each trace, runs are printed in execution order with **depth** indicating nesting:
  - `depth=0` — root (the top-level chain triggered by user input)
  - `depth=1` — direct child (e.g. an llm call or tool call spawned by the root)
  - `depth=2+` — nested further

#### Run types

Each run has a `run_type`: `llm`, `tool`, `chain`, etc.

For `run_type=llm` runs, the report prints full input messages and outputs.

- **System message is omitted** — it is identical across all runs, so excluded to save space.
- Each remaining message shows its `role` and compact `kwargs` structure.
- Verbose tools `read_file`, `write_file`, `run_trace_report_async`, `summarize_traces_async`, `ls`, `edit_file`, `grep`, `execute`) have `content` intentionally redacted as `[redacted — N chars]` to avoid context bloat.
- Focus analysis on flow and issues (especially tool choice and order). Do not rely on raw tool output in traces: outputs are usually long and low-signal; e.g. for `run_trace_report_async` / `summarize_traces_async` the outputs are already used/logged, and for `execute` the implemented code is reflected in later messages and can be verified from local files.

### 2. Summarize

Call `summarize_traces_async(report, offset=N, limit=50)` in batches.
Use `report["trace_count"]` to compute how many calls are needed (`ceil(trace_count / 50)`).

Fire all batches in parallel: each with `limit=50` and `offset=0, 50, 100, …`
The last batch will naturally contain the remainder.

- **Do not raise `limit` above 50** — larger batches will hit Haiku's context limit
- **Never re-fetch if summarization fails** — adjust `offset`/`limit` and retry the failed batch only

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

After report is approved and changes committed:

1. Append to `trace_analysis_log.md`:

```markdown
## 2026-05-01T14:30:00Z
- Project: my-agent
- Traces analyzed: 42 (last 7 days)
- Key findings: sha256_mismatch (6), hitl_rejected (3)
- Changes committed: updated AGENTS.md Known Pitfalls
```

1. If `offload=True` was used, delete the offloaded trace file:

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
