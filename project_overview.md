## Paper2Wiki — Capabilities & Workflows

---

## Capabilities

### 1. Ingest
Accept any of:
- PDF file upload
- arXiv URL / DOI (only implement this this phase)
- Web article URL (Obsidian Web Clipper format)
- Topic name (agent searches and fetches)

Phase 1:  fetch arXiv too: can Search and fetch paper by arXiv ID, URL, or topic name >> save to raw/papers/slug.pdg
And then parse the pdf saved, also save extracted images to raw/assets/slug/.. 

Produces:

- Paper page with structured sections
- Concept pages (created/updated)
- Entity pages (authors, institutions)
- Citation graph JSON (wiki/graph/citations.json)
- graph.json upserted (wiki/graph/graph.json) — nodes + edges with EXTRACTED/INFERRED tags
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

Optional triggers:
- "show graph" → Code subagent generates wiki/graph/graph.html (vis.js, self-contained)
- "show graph for <topic>" → filtered subgraph HTML for that concept cluster
---
The key changes:

Ingest now explicitly upserts graph.json as a separate artifact from citations.json (citations = raw refs, graph = full concept+relationship network)
Query traverses graph first instead of guessing from index.md
graph.html generation moved to Query as an on-demand trigger, not something rebuilt every ingest — no point regenerating HTML if nobody asked to see it

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
→ run_lint() → fix issues if any, checks only files just written
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
→ run_lint() for programmatic checks # checks entire wiki
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

So update your workflows to:
# Ingest
→ run_lint(files=newly_written) → fix issues if any

# Health check  
→ run_lint() for programmatic checks

---

## TODOs (deferred)

**Tables from PDFs**

- The parser can save each table as a **picture** (`raw/assets/<slug>/tables/table_000.png`, …).
- The main **export markdown** file does **not** include those pictures inside it. It only links **figures** (diagrams, etc.). Tables in that file are plain **markdown tables**, which often don’t match the PDF (merged cells, spacing, math).
- **Later:** when we build wiki paper pages, we could swap markdown tables for `![caption](path)` using the saved table pictures instead.

**For now:** we are **not** doing that. We only rely on the export `.md` for text and figure images; table PNGs are optional output we can ignore until we care.

Yes — the idea is ![caption](path) = one image that stands in for the whole table, and that can subsume “images inside the table” because the crop is the whole layout. It is not a different feature called “images embedded in table”; it’s the same table-as-image approach.
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