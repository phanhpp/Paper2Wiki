# Paper2Wiki — Capabilities & Workflows

---

## When to delegate to subagents:

- Task would flood supervisor context with intermediate data: multi-file refactoring, large codebases
- Reasoning-heavy subtasks (debugging, code review, research synthesis)
- Parallel independent workstreams (research A and B simultaneously)
- Fresh-context tasks where you want the agent to approach without bias
- When supervisor load a skill that contains explicity instructions to delegate to subagents

Example patterns:

- Code Review: Delegate a security review to a fresh-context subagent that approaches the code without preconceptions
- Evaluate multiple approaches to the same problem in parallel, then pick the best
- Split a large refactoring task across parallel subagents, each handling a different part of the codebase
- Use execute_code for mechanical data gathering, then delegate the reasoning-heavy analysis >> This is often the most efficient pattern: execute_code handles the 10+ sequential tool calls cheaply, then a subagent does the single expensive reasoning task with a clean context.

Note: 
- give subagents the only tools they need
- even when to delegation is a SKILL.md: `https://github.com/NousResearch/hermes-agent/blob/main/skills/software-development/subagent-driven-development/SKILL.md?plain=1` 

## What keeps it in the parent

Simple edits — single file, few lines
Task needs conversation history — subagent starts fresh, knows nothing
Task needs user clarification — subagents can't call clarify
Task needs memory writes — blocked for subagents
Short enough to not matter

## Capabilities

### 1. llm-wiki (as a skill) (Supervisor)

#### Ingest

Accept any of:
- PDF file upload
- arXiv URL / DOI (only implement this this phase)
- Web article URL (Obsidian Web Clipper format)
- Topic name (agent searches and fetches)

Produces:
- Paper page with structured sections
- Concept pages (created/updated)
- Entity pages (authors, institutions)
- Citation graph JSON (wiki/graph/citations.json)
- graph.json upserted (wiki/graph/graph.json) — nodes + edges with EXTRACTED/INFERRED tags
- Updated index.md + log.md
- lint.py passes
- Git commit (HITL before push)

#### Query

Accept: natural language question about ingested papers

Produces:

- Synthesized answer citing wiki pages
- If valuable → filed back as `/wiki/comparisons/` or `/wiki/syntheses/` page
- Updated index.md + log.md

#### Wiki Health Check

Triggered by: "run wiki health check"
Loads: `wiki-health` skill

Checks:
- Broken [[wikilinks]]
- Orphan pages (no inbound links)
- Contradictions between pages
- Concepts mentioned but no dedicated page
- Missing required sections
- Stale index.md entries

---

### 3. Code Tasks (sandbox + HITL)

- "generate mermaid diagram of architecture"
→ Handle directly. Supervisor already has the paper content in context. Mermaid is just text generation — write to wiki/graph/diagram.md. No sandbox needed, no flooding risk.
- "plot the training curves from figure 4"
→ Delegate to Code subagent. Requires actual code execution (matplotlib/plotly), sandbox, file output. Exactly what Code subagent is for.
- "generate slides from this paper"
→ Delegate to Code subagent. Marp slide deck generation requires running code, producing output files, potentially HITL on the result.
- "show graph" / "show graph for topic"
→ Delegate to Code subagent. Generates wiki/graph/graph.html via vis.js — requires code execution, reading wiki index to build graph data, writing output file.

git action - as
BUT: 

- Mermaid as raw text in a markdown page → supervisor handles directly.
- But if user says "render this mermaid to PNG" or "preview the diagram" → delegate, because that needs execution.

Delegate to Code subagent when:

- Task produces a file artifact (HTML, PNG, PDF, .marp)
- Task requires code execution or sandbox
- Task reads multiple wiki files to compute output

Handle directly when:

- Output is markdown/text written to wiki/
- Task is pure LLM generation (mermaid as text, summaries)
- Single file read + write

---

### 5. Self-Improvement (Supervisor)

Triggered by: "analyze traces and improve"
Loads: `trace-analyzer` skill

Flow:
1. fetch LangSmith traces (analyze.py)
2. parallel error analysis:  multiple websearch - supervisor can do itself
3. synthesize failure patterns
4. rewrite `/skills/` SKILL.md files (HITL before edits)
5. optionally update AGENTS.md (HITL before edits)

---

## Workflows

### Ingest Workflow
```
User provides source
→ Supervisor delegates to Ingest subagent
→ fetch_arxiv() OR parse_pdf() OR fetch_web_article()
→ download_image() for inline images (read text first)
→ write wiki/papers/<slug>.md
→ create/update concept pages
→ create/update entity pages
→ write citation graph to wiki/graph/
→ update wiki/index.md
→ append wiki/log.md
→ run_lint() → fix issues if any, checks only files just written
→ git_commit_and_push() → HITL → push
```

### Self-Improvement Workflow
```
User: "analyze traces and improve"
→ Supervisor loads trace-analyzer skill
→ runs analyze.py → fetches LangSmith traces
→ parallel analysis of failures
→ synthesizes patterns:
   "mermaid subgraph syntax failing 3x"
   "citation extraction missing DOI"
→ rewrites relevant SKILL.md files
  → HITL: approve/reject each edit
→ git commits skill changes
→ next session: agent uses improved skills
```

## System prompt

Note: patch a skill immediate if it's wrong not waiting to be asked

SKILLS_GUIDANCE = (
    "After completing a complex task (5+ tool calls), fixing a tricky error, "
    "or discovering a non-trivial workflow, save the approach as a "
    "skill with skill_manage so you can reuse it next time.\n"
    "When using a skill and finding it outdated, incomplete, or wrong, "
    "patch it immediately with skill_manage(action='patch') — don't wait to be asked. "
    "Skills that aren't maintained become liabilities."
)

## Skills to take from Hermes:

- https://github.com/NousResearch/hermes-agent/blob/main/skills/software-development/subagent-driven-development/SKILL.md?plain=1
- https://github.com/NousResearch/hermes-agent/blob/main/skills/research/llm-wiki/SKILL.md?plain=1
- https://github.com/NousResearch/hermes-agent/blob/main/skills/research/arxiv/SKILL.md?plain=1

## Fix lint part in skill:

- Keep lint.py script 
- Make wiki-health check as part of the skill

```
lint.py (programmatic):          LLM judgment (wiki-health skill):
- orphan pages                   - semantic contradictions
- broken wikilinks               - confidence/quality assessment  
- index completeness             - stale content (meaning-level)
- frontmatter field presence     - "this concept needs its own page"
- tag not in taxonomy
- page size >200 lines
- log rotation threshold
- sha256 drift on raw/
```

# Tests

- test fetch_arxiv really saved file

# TODOs:

- make use of tables and images
- ingest anything
- create auto tests