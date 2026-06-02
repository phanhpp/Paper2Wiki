# observability_eval_tools

Self-improvement pipeline for Paper2Wiki. Fetches LangSmith traces, detects
anomalies, and maintains evaluation datasets that gate the weekly CI regression
suite.

## Module map

```
fetch_traces.py              summarize_traces.py
  run_trace_report_async()     summarize_traces_async()
        |                             |
        |  TraceReport                |  (human-readable summaries,
        |                             |   HITL via trace-analysis skill)
        v
anomaly_detection.py
  compute_baselines_async()  ──► memories/baselines.json   (weekly CI, auto)
  detect_anomalies_async()   ──► AnomalyReport             (HITL via skill)
        |
        |  AnomalyReport
        v
create_eval_datasets.py
  create_datasets_from_anomaly_report()
        |
        ├──► LangSmith datasets  (one dataset per run_type/run_name/context)
        └──► suggested eval/cases.json entries (HITL, written after approval)
        |
        v
evaluation_tools.py
  build_evaluators_for_signals()
  run_evaluate()             ──► LangSmith experiment results
  apply_composite_scores()
```

## Data flow

```
LangSmith API
      |
      | run_trace_report_async(project, days, limit)
      v
  TraceReport
    .traces         dict[trace_id, formatted_string]  (inline when small)
    .traces_path    Path to JSON offload file          (when > size threshold)
    .trace_count    total traces fetched
    .is_offloaded   True when traces written to disk
      |
      |-- compute_baselines_async(report)
      |       Parses runs, computes per-name median latency/tokens and
      |       per-flow median step counts. Writes memories/baselines.json.
      |       Runs weekly (CI) so baselines stay fresh.
      |
      |-- detect_anomalies_async(report)
              Loads memories/baselines.json. Flags any run exceeding 3x
              baseline on latency, tokens, or step count. Always flags
              hard errors regardless of baselines.

              What gets flagged (by run_type):
                tool        hard_error, latency_spike, step_count_spike
                llm         hard_error, latency_spike, token_blowout
                chain       hard_error, latency_spike — EXCEPT:
                  name=="LangGraph"  top-level framework span, excluded
                                     (infra noise: OOM, state corruption)

              Also excluded:
                eval runs   ls_experiment_id in metadata
                model / tools / ChatAnthropic  excluded from baseline
                            computation only; hard errors still fire

              Signal format:
                hard_error:<first error line>
                latency_spike:<actual>s_vs_median_<median>s
                token_blowout:<actual>_vs_median_<median>
                step_count_spike:<flow>:<actual>_vs_median_<median>
              |
              v
          AnomalyReport
            .total_runs_analyzed
            .anomalous_run_count
            .anomalies  list[AnomalySignal]
              AnomalySignal
                .trace_id
                .errors   ["hard_error", "latency_spike", ...]
                .signals  ["hard_error:HTTPError 429...", ...]
                .failed_spans  list[FailedSpan]
                  FailedSpan
                    .id, .run_name, .run_type, .flow
                    .inputs, .outputs
                    .errors, .signals
```

## Who calls what

| Caller | Tools used | When |
|---|---|---|
| `eval/run_weekly.py` | `run_trace_report_async`, `compute_baselines_async` | Weekly CI — baseline refresh only |
| `pytest -m langsmith` | reads LangSmith datasets populated by HITL | Weekly CI — regression gate |
| `trace-analysis` skill | all tools in order, with HITL between each step | On-demand (`"Analyze my traces"`) |
| `eval/run_gate.py` | imports tools from `src/tools/` directly | Every PR — deterministic tool-level gate |

## Adding a new tool to monitoring

1. Add the tool name → flow label in `TOOL_FLOWS` (anomaly_detection.py).
2. Add the tool name → eval category in `_TOOL_EVAL_CATEGORY` (create_eval_datasets.py).
3. Add at least one case to `eval/cases.json` covering the happy path.
