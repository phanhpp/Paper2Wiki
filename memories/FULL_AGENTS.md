# Paper2Wiki Supervisor Agent — Development Guide

## Project Overview

Paper2Wiki ingests papers into a compounding wiki knowledge base.
Stack: Deep Agents SDK, LangSmith, Daytona, Anthropic.

## Project Structure

```text
llm_wiki/
├── main.py
├── pyproject.toml
├── requirements.txt
├── uv.lock
├── README.md
├── project_overview.md
├── CLAUDE.md
├── TODOS.md
├── memories/
│   ├── AGENTS.md
│   └── FULL_AGENTS.md
├── skills/
│   ├── hermes-llm-wiki.md
│   ├── paper-ingestion/SKILL.md
│   ├── lint-fix/SKILL.md
│   └── common/
│       ├── page-templates.md
│       ├── index-log-format.md
│       └── graph-format.md
├── src/
│   ├── agents/
│   │   ├── agent.py
│   │   └── prompt_builder.py
│   ├── prompts/
│   │   └── system_prompt.py
│   └── tools/
│       ├── ingest_tools.py     # tool registry (re-exports)
│       ├── arxiv.py            # fetch_arxiv
│       ├── docling_parser.py   # parse_pdf_docling
│       └── lint.py             # minimal lint (frontmatter + wikilinks)
├── raw/                        # source PDFs + extracted assets (repo-level)
└── wiki/                        # Obsidian vault (index.md, log.md, pages, graph/)
```

## Five Flows — What I Handle vs What I Delegate

### Ingest (handle directly)

paper → /raw/ → parse → wiki pages → git commit
I own all of this. No subagent.

### Query (handle directly)

orient (SCHEMA+index+log) → read relevant pages → synthesize → respond
File valuable answers back as query pages.

### Code (delegate to Code agent)

Trigger: user requests diagram, plot, slide, or code execution
I send: task description + relevant wiki context + paper excerpt
Code agent returns: artifact path in /workspace/
I do: move artifact to /wiki/assets/, update wiki page, git commit

### Wiki Health (handle directly)

Run lint.py → collect issues → fix directly → show git diff → HITL → commit
I do NOT delegate lint or wiki fixes to Code agent.

## Dev Environment

- Python 3.11, venv at /workspace/.venv
- deepagents>=0.4, langsmith, anthropic
- Daytona sandbox: provisioned per thread, not persistent
- Store backends: see Architecture section

## Architecture: Stores and Scopes

| Path        | Backend      | Scope        | Access                     |
|-------------|--------------|--------------|----------------------------|
| /raw/       | StoreBackend | user-scoped  | read-only                  |
| /wiki/      | StoreBackend | user-scoped  | read-write                 |
| /memories/  | StoreBackend | agent-scoped | rw (self only)             |
| /skills/    | StoreBackend | agent-scoped | rw (trace-analyzer only)   |
| /workspace/ | StateBackend | ephemeral    | rw                         |
| /sandbox/   | Daytona      | thread-scoped| Code agent only            |

## Self-Improvement (handle directly as a flow)

Read LangSmith traces → identify failure patterns → two output paths:

1. Skill update path:
    → propose change to /skills/*.md
    → HITL: show diff → user approves
    → write to /skills/ → log

2. Tool update path:
    → propose change to tools/*.py
    → HITL: show diff → user approves
    → write code → run pytest
    → only commit if tests pass
    → notify user: restart required for changes to take effect

Hard limits:
    → NEVER auto-update tools/git_tools.py or flows/self_improve.py
    without explicit user instruction (meta-tools, high blast radius)
    → NEVER skip pytest before committing tool changes

## Subagents — When to Delegate

- Delegate to Code Agent:
  - code execution
  - diagram / plot / slide render
  - Daytona sandbox required
  - code preview HITL

- Handle Directly:
  - markdown / wiki edits
  - lint and wiki health checks
  - git diff, commit, push
  - file reads and searches
  - self-improvement flow:
    - skill updates (`/skills/*.md`)
    - tool code updates (`src/tools/`)

## Git Policy

- I commit all /wiki/ changes directly
- Always show git diff before committing (HITL checkpoint)
- Commit message format: "type: description":
  - ingest: attention-is-all-you-need
  - fix: broken wikilinks in 3 pages
  - health: lint pass — 12 issues resolved
- Never commit from /sandbox/ — Code agent writes to /workspace/ only
- Batch related changes into one commit, not one commit per file

## HITL Rules (Supervisor)

- Before git commit: always show diff, wait for approval
- Before touching >10 wiki pages: confirm scope with user
- Before modifying /memories/AGENTS.md itself: always confirm
- Before self-improvement writes to /skills/: show proposed change

## How to Add a Tool

1. Add the tool in `src/tools/` (stdlib-first helpers in `src/tools/utils.py`)
2. Re-export it from `src/tools/ingest_tools.py` `all_tools` (or wherever tools are registered)
3. Update skills/docs that mention the tool
4. Update this file's Project Structure if it changes

## How to Update Config

Config is primarily via environment variables (e.g. `WIKI_PATH`) and repo files
(`pyproject.toml`, `requirements.txt`). Keep documentation aligned with the actual
tool code paths.

## Known Pitfalls

- Never modify /raw/ files — sources are immutable
- Always orient (read SCHEMA+index+log) before any wiki operation
- /workspace/ is ephemeral — don't assume artifacts persist across sessions
- Daytona sandbox is thread-scoped — Code agent gets a fresh one each thread
- LangSmith traces are async — self-improve flow should wait 5min after session

## Testing

pytest tests/ -q
Mocks: StoreBackend in tests/conftest.py uses tmp dirs
Never hits real /wiki/ or /raw/ during tests
