# Tests

The layout mirrors `src/`, so a test lives next to nothing but its subject:

```
tests/
├── agents/       ← src/agents/      stream, interrupts, the guarded backend
├── cli/          ← src/cli/         commands, config, keys, setup
├── connectors/   ← src/connectors/  the fetch contract, http, git-repo
├── middleware/   ← src/middleware/  Loop 2 checks
├── sessions/     ← src/sessions/
├── slack/        ← src/slack/
├── tools/        ← src/tools/       ingest, wiki, trace and eval tools
├── fixtures/                        recorded data (runs.json, mock reports)
└── scripts/                         one-shot helpers, not tests
```

Four files stay at the top level because they are not about one package:

| file | why |
|---|---|
| `test_paths.py` | `src/paths.py` sits at the src root |
| `test_llm_roles.py` | so does `src/llm_roles.py` |
| `test_provider_agnostic.py` | cuts across `cli`, `llm_roles` and `eval/` |
| `test_no_secrets_in_repo.py` | scans the whole repo |

The last two derive the repo root from their own `__file__`, so moving them into a
subdirectory would silently change what they scan.

## Running

```bash
uv run pytest -m unit -q          # fast, mocked, no network — what CI runs on every PR
uv run pytest tests/cli -q        # one area
uv run pytest -m "not integration"
```

`conftest.py` pins tracing off, colour off and a fixed terminal width at *import* time —
not in a fixture. A fixture runs after collection, by which point `langsmith` has cached
its env lookup and Rich has measured the terminal.

## Adding a test

Put it in the directory matching the `src/` package it covers, mark it `@pytest.mark.unit`
if it needs no network, and mock I/O. If it asserts on CLI output, assert on the command
object (`param.opts`) rather than rendered help — colour codes split option names, which
is why the suite once passed locally and failed in CI.
