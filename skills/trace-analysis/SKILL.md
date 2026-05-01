<!-- when to trigger trace analysis
fetch phase (list_runs, filters, watermark)
summarize phase (parallel tool calls, cheap model, structured output)
clustering/synthesis
git commands for reading history + committing approved changes
HITL checkpoints -->
# Trace Analysis Skill

## Trace Report (`run_trace_report`)

Call `run_trace_report(project, days, limit)` to inspect recent agent runs from LangSmith.

### Output structure

- Runs are grouped by **trace** (one trace = one user request, spanning all its sub-operations).
- Within each trace, runs are printed in execution order with **depth** indicating nesting:
  - `depth=0` — root (the top-level chain triggered by user input)
  - `depth=1` — direct child (e.g. an llm call or tool call spawned by the root)
  - `depth=2+` — nested further

### Run types

Each run has a `run_type`: `llm`, `tool`, `chain`, etc.

For `run_type=llm` runs:

- Only for this type does the report print full input messages and outputs.
- **System message is omitted** — it is identical across all runs, so excluded to save space.
- Each remaining message shows its `role` and full `kwargs` structure.
- `read_file` / `write_file` tool message content is replaced with `[redacted — N chars, M words]`.
- If redacted content contains `"error"` (case-insensitive), full content is kept and prefixed with `[ERROR]`.

### Full format

Each run dict in `trace_runs` has these fields (some may be None):

- `id` — string, run ID
- `trace_id` — string, which trace this belongs to
- `name` — string, e.g. "LangGraph", "ChatAnthropic", "TodoListMiddleware.after_model"
- `run_type` — string e.g. "llm", "tool", "chain"
- `status` — string, one of "success", "error", "interrupted"
- `error` — string or None, full error message with traceback
- `depth` — int, 0 = root run, 1 = direct child, 2 = grandchild etc
- `start_time` — string ISO timestamp
- `end_time` — string ISO timestamp
- `latency` — float, seconds
- `total_tokens` — int
- `total_cost` — float
- `tags` — list of strings
- `inputs` — dict or None. Only populated for run_type="llm" runs (fetched separately via read_run). Always None for chain and tool runs.
- `outputs` — dict or None. Same as inputs.

---
<!-- Todo: create summary tool: summarize_trace() -->
## Summarization Phase

After fetching traces with `run_trace_report`, summarize each trace 

### When to use parallel tool calls vs subagents

- ≤50 traces → parallel tool calls to `summarize_trace` (cheap model, e.g. Haiku)
- >50 traces → delegate batches of ~25 to subagents, each subagent uses `summarize_trace` internally, returns batch summaries to supervisor

### What `summarize_trace` should extract per trace

Call a cheap/fast model with the trace content. Extract structured output:

```json
{
  "trace_id": "...",
  "session_summary": "User asked X, agent did Y, result was Z",
  "status": "success | error | interrupted",
  "error_type": "sandbox_timeout | tool_failure | hitl_rejected | none",
  "recoverable": true,
  "affected_skill": "llm-wiki | none",
  "skill_compliance": "followed | deviated | unclear",
  "deviation_note": "agent skipped lint step despite skill requiring it"
}
```

---

## Clustering & Synthesis

Supervisor (Opus) receives all structured summaries and:

1. Groups by `error_type` and `skill_compliance`
2. Counts frequency of each pattern
3. Ranks findings by frequency + severity
4. Produces a concise summary of findings e.g.:

```
## Trace Analysis Report — last 7 days (N sessions)

### Findings (ranked)

1. sandbox_timeout (8 traces, 11%) — agent called code tool, Daytona sandbox failed to init
2. skill_deviation:lint_skipped (5 traces, 7%) — agent completed ingest without running lint
3. hitl_rejected (3 traces, 4%) — user rejected tool call, agent did not recover gracefully

### Recommendations
- Update llm-wiki SKILL.md: add explicit lint requirement after every ingest
- Add sandbox retry logic to Code subagent
```

---

## HITL Checkpoints

**Always require approval before:**
- Writing to `/skills/` 
- Writing to `/memories/AGENTS.md`
- `git commit`
- `git push`

**Never require approval for:**
- Reading traces (`run_trace_report`)
- Summarizing traces
- Reading git history

Show the full diff of proposed changes to AGENTS.md or skill files before asking for approval.

---

## Git Commands

Always use `--no-pager` and `-n <limit>` to avoid pager hanging.

**Allowed (no HITL):**
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

**Allowed (HITL required):**
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
<!-- Todo: Determine if fetch log and watermark should be in the same log file -->
## Watermark

After a completed analysis cycle (traces fetched → summarized → report approved → changes committed), save a timestamp to avoid re-analyzing the same traces next run:

```python
# read
with open(".trace_analyzer_watermark", "r") as f:
    last_run = datetime.fromisoformat(f.read().strip())

# write (only after HITL approval + git commit)
with open(".trace_analyzer_watermark", "w") as f:
    f.write(datetime.now(timezone.utc).isoformat())
```

Pass `start_time=last_run` to `run_trace_report` on the next cycle.

Add `.trace_analyzer_watermark` to `.gitignore`.
```