# `eval/fixtures/`

## `mock_anomaly_report.json`

A hand-built `AnomalyReport` for exercising **step 4 of the trace-analysis skill**
(`create_datasets_from_anomaly_report`) without waiting for a real production failure.

Why it exists: the promote-to-PR-gate step was added to the skill *after* the last real
trace-analysis run, and there have been no hard errors since — so that path has never
actually run end to end.

### What it contains, and why

Four spans, chosen so the filtering is visible in the output:

| Span | Signal | run_type | Expected |
|---|---|---|---|
| `aaaa…` `fetch_arxiv` 429 | `hard_error` | tool | dataset **and** a suggested gate case |
| `bbbb…` `ChatAnthropic` 529 | `hard_error` | **llm** | dataset only — gate cases are tool-only |
| `cccc…` `fetch_arxiv` slow | `latency_spike` | tool | dataset only — not a hard error |
| `dddd…` `fetch_arxiv` bad id | `hard_error` | tool | gate case, and the signal text should lead the skill to infer `expect_error: true` |

**Expect exactly two `suggested_cases`.** If you get four, the `run_type == "tool"` filter
broke; if you get one, the hard-error filter did.

### Checking the filtering only — offline, no LangSmith

```bash
uv run python -c "
import json
from src.tools.observability_eval_tools.anomaly_detection import AnomalyReport
from src.tools.observability_eval_tools.create_eval_datasets import _generate_PR_cases
raw = json.load(open('eval/fixtures/mock_anomaly_report.json')); raw.pop('_comment')
for c in _generate_PR_cases(AnomalyReport(**raw)): print(c['id'], c['inputs'])
"
```

Verified output:
```
regression_fetch_arxiv_aaaaaaaa {'query': '1706.03762'}
regression_fetch_arxiv_dddddddd {'query': 'INVALID999'}
```

### Driving the whole skill step with the agent

This **does** hit LangSmith and creates real datasets, so use a scratch project.

```
Read eval/fixtures/mock_anomaly_report.json and treat it as the output of
detect_anomalies_async. Skip steps 1-3 of the trace-analysis skill (no fetching,
no summarising, no detecting) and go straight to step 4: call
create_datasets_from_anomaly_report with eval_cases=True, then present the
suggested cases for approval exactly as the skill describes.
```

What to watch for:

1. **Two** suggested cases, not four.
2. Each carries `_review` and **no** `expect_*` — the tool must not invent an assertion.
3. The skill infers one per case from `span.signals`: `INVALID999` reads as bad input →
   `expect_error: true`; the 429 on a valid id reads as a transient failure on good input
   → `expect_keys: [...]`.
4. **A HITL prompt fires on the `write_file` to `eval/pr_gate_cases.json`** — and only
   there. Pushing to LangSmith fires no prompt (`interrupt_on` covers `execute`,
   `write_file`, `edit_file` only — `agent.py:208`).
5. Approved cases land with `_review` **stripped**.

Decline the write unless you actually want these fake cases in the gate.

### Cleaning up

Datasets are named `{scope}__rt_{run_type}__rn_{run_name}` — delete them in the LangSmith
UI afterwards, or run against a throwaway `LANGSMITH_PROJECT`.
