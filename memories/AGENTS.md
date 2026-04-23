# Paper2Wiki Supervisor Agent — Development Guide

## Project Overview

Paper2Wiki ingests papers into a compounding wiki knowledge base.
Stack: Deep Agents SDK, LangSmith, Daytona, Anthropic.

## Project Structure

```
paper2wiki/
├── agents/
│   ├── supervisor.py      # Main orchestrator (this agent)
│   ├── code_agent.py      # Sandbox executor
│   └── prompts/
│       ├── supervisor_system.py
│       └── code_system.py
├── flows/
│   ├── ingest.py          # papers → wiki
│   ├── query.py           # wiki → answer
│   ├── health.py          # lint, contradictions
│   └── self_improve.py    # traces → skills
├── tools/
│   ├── wiki_tools.py      # read/write/search wiki
│   ├── git_tools.py       # diff, commit, log
│   └── lint.py            # broken links, orphans
├── skills/                # loaded by code agent
│   ├── mermaid.md
│   ├── plotting.md
│   └── marp.md
└── tests/
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

| Path             | Backend      | Scope        | Access         |
|------------------|--------------|--------------|----------------|
| /raw/            | StoreBackend | user-scoped  | read-only      |
| /wiki/           | StoreBackend | user-scoped  | read-write     |
| /memories/       | StoreBackend | agent-scoped | rw (self only) |
| /skills/         | StoreBackend | agent-scoped | rw (trace-analyzer flow only) |
| /workspace/      | StateBackend | ephemeral    | rw             |
| /sandbox/        | Daytona      | thread-scoped| Code agent only|

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

| Delegate to Code Agent         | Handle Directly                |
|--------------------------------|--------------------------------|
| code execution                 | markdown / wiki edits          |
| diagram / plot / slide render  | git diff, commit, push         |
| Daytona sandbox required       | lint and wiki health checks    |
| code preview HITL              | file reads and searches        |
|                                | self-improvement flow:         |
|                                |  - skill updates (/skills/*.md)|
|                                |  - tool code updates (tools/*.py)

## Git Policy

- I commit all /wiki/ changes directly
- Always show git diff before committing (HITL checkpoint)
- Commit message format: "type: description":
    * ingest: attention-is-all-you-need
    * fix: broken wikilinks in 3 pages
    * health: lint pass — 12 issues resolved
- Never commit from /sandbox/ — Code agent writes to /workspace/ only
- Batch related changes into one commit, not one commit per file

## HITL Rules (Supervisor)

- Before git commit: always show diff, wait for approval
- Before touching >10 wiki pages: confirm scope with user
- Before modifying /memories/AGENTS.md itself: always confirm
- Before self-improvement writes to /skills/: show proposed change

## How to Add a Tool

1. Add function to tools/wiki_tools.py or tools/git_tools.py
2. Register in supervisor.py tool list
3. Add test in tests/test_tools.py
4. Update this AGENTS.md under Project Structure

## How to Update Config

Config lives in paper2wiki/config.yaml:

- wiki_path, raw_path, memories_path → store routing
- langsmith_project → trace destination
- daytona_workspace → sandbox config

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