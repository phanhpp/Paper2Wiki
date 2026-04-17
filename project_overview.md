## Paper2Wiki — Capabilities & Workflows

---

## Capabilities

### 1. Ingest
Accept any of:
- PDF file upload
- arXiv URL / DOI
- Web article URL (Obsidian Web Clipper format)
- Topic name (agent searches and fetches)

Produces:
- Paper page with structured sections
- Concept pages (created/updated)
- Entity pages (authors, institutions)
- Citation graph JSON
- Updated index.md + log.md
- lint.py passes
- Git commit (HITL before push)

---

### 2. Query
Accept: natural language question about ingested papers

Produces:
- Synthesized answer citing wiki pages
- If valuable → filed back as `/wiki/comparisons/` or `/wiki/syntheses/` page
- Updated index.md + log.md

---

### 3. Code Tasks (sandbox + HITL)
Accept: user request to do something FROM paper content:
- "generate mermaid diagram of architecture"
- "plot the training curves from figure 4"
- "generate slides from this paper"

Produces:
- Code executed in Daytona sandbox (thread-scoped)
- HITL approval before execute/write_file/edit_file
- Output: `.mmd`, `.svg`, `.py`, `.pdf`, `.png`
- Mermaid rendered via mmdc → static SVG (XSS safe)
- Marp rendered via marp-cli → PDF/HTML slides
- Output saved to `/wiki/outputs/`

---

### 4. Wiki Health Check (manual)
Triggered by: "run wiki health check"
Loads: `wiki-health` skill

Checks:
- Broken [[wikilinks]]
- Orphan pages (no inbound links)
- Contradictions between pages
- Concepts mentioned but no dedicated page
- Missing required sections
- Stale index.md entries

Produces: `wiki/health/health_report_YYYY-MM-DD.md`

---

### 5. Self-Improvement (manual)
Triggered by: "analyze traces and improve"
Loads: `trace-analyzer` skill

Flow:
1. fetch LangSmith traces (analyze.py)
2. spawn parallel error analysis
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
→ run_lint() → fix issues if any
→ git_commit_and_push() → HITL → push
```

### Query Workflow
```
User asks question
→ Supervisor delegates to Query subagent
→ read wiki/index.md
→ read relevant pages and compare/cross-verify content if needed
→ search web for external info
→ synthesize answer
→ if valuable: write back to wiki/comparisons/ or wiki/syntheses/
→ update index.md + log.md
→ git_commit_and_push() if wiki updated → HITL
```

### Code Workflow
```
User requests coding task
→ Supervisor delegates to Code subagent
→ reads relevant wiki page / raw paper via StoreBackend (Plane 1)
→ writes code file
  → HITL: approve/edit/reject write_file
→ executes in Daytona sandbox
  → HITL: approve/edit/reject execute
→ mmdc/marp-cli renders output
→ saves to wiki/outputs/
→ git_commit_and_push() → HITL
```

### Wiki Health Workflow
```
User: "run wiki health check"
→ Supervisor loads wiki-health skill
→ LLM reads entire wiki
→ checks orphans, contradictions, missing pages
→ run_lint() for programmatic checks
→ writes wiki/health/health_report_YYYY-MM-DD.md
→ presents findings to user
→ user decides which fixes to apply
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

---

## HITL Gates

```
Ingest:  git_commit_and_push → approve/reject
Code:    write_file           → approve/edit/reject
         edit_file            → approve/edit/reject
         execute              → approve/edit/reject
Improve: edit_file on skills  → approve/reject
         write_file on skills → approve/reject
```

---

## Storage Per Path

```
/raw/              StoreBackend, user-scoped,      read-only
/wiki/             StoreBackend, user-scoped,      read-write
/memories/AGENTS.md StoreBackend, agent-scoped,    read-only by agent
/memories/preferences.md  StoreBackend, user-scoped,     read-write
/skills/           StoreBackend, agent-scoped,     read-write (trace-analyzer)
/workspace/        StateBackend, ephemeral scratch
/sandbox/          Daytona,      thread-scoped,    Code subagent only
```

---

## Skills Per Subagent

```
Ingest subagent:
  paper-ingestion    ← all input types, wiki page creation,
                        wikilinks, index, log, lint, git,
                        image handling, citation graph 
                        (fetch web page, search paper through axvir, parse pdf/ image, run lint.py ..)

Code subagent:
  mermaid            ← diagram syntax, mmdc, XSS-safe rendering
  plotting           ← matplotlib/plotly from paper data
  marp               ← slide deck generation

Supervisor (manual):
  trace-analyzer     ← self-improvement outer loop
  wiki-health        ← full wiki health check
```