---
name: llm-wiki
description: "Karpathy's LLM Wiki — build and maintain a persistent, interlinked markdown knowledge base. Ingest sources, query compiled knowledge, and lint for consistency."
version: 0
author: Hermes Agent - modified by Phanhpp
license: MIT
metadata:
  Paper2Wiki:
    tags: [wiki, knowledge-base, research, notes, markdown, rag-alternative]
    category: research
---

<!-- markdownlint-disable MD013 -->

# Karpathy's LLM Wiki

Build and maintain a persistent, compounding knowledge base as interlinked markdown files.
Based on [Andrej Karpathy's LLM Wiki pattern](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f).

Unlike traditional RAG (which rediscovers knowledge from scratch per query), the wiki compiles knowledge once and keeps it current. Cross-references are already there. Contradictions have already been flagged. Synthesis reflects everything ingested.

## When This Skill Activates

Use this skill when the user:

- Asks to create, build, or start a wiki or knowledge base
- Asks to ingest, add, or process a source into their wiki
- Asks a question and an existing wiki is present at the configured path
- Asks to lint, audit, or health-check their wiki
- References their wiki, knowledge base, or "notes" in a research context

## Wiki Location

**Location:** Set via `WIKI_PATH` environment variable (e.g., in the project's `.env` file).

If unset, defaults to the `wiki` folder within the project root.

```bash
# Logic to find the project root and default the path
WIKI="${WIKI_PATH:-$(pwd)/wiki}"
```

The wiki is just a directory of markdown files — open it in Obsidian, VS Code, or
any editor. No database, no special tooling required.

## Architecture: Three Layers

```text
wiki/
├── SCHEMA.md           # Conventions, structure rules, domain config
├── index.md            # Sectioned content catalog with one-line summaries
├── log.md              # Chronological action log (append-only, rotated yearly)
├── raw/                # Layer 1: Immutable source material
│   ├── articles/       # Web articles, clippings (<slug>.md)
│   ├── papers/         # Research papers, arXiv, PDFs (<slug>.md — web_extract returns markdown directly)
│   └── transcripts/    # Meeting notes, interviews, pasted text
├── entities/           # Layer 2: Entity pages (people, orgs, products, models)
├── concepts/           # Layer 2: Concept/topic pages
├── comparisons/        # Layer 2: Side-by-side analyses
└── queries/            # Layer 2: Filed query results worth keeping
```

**Layer 1 — Raw Sources:** Immutable. The agent reads but never modifies these.
**Layer 2 — The Wiki:** Agent-owned markdown files. Created, updated, and
cross-referenced by the agent.
**Layer 3 — The Schema:** `SCHEMA.md` defines structure, conventions, and tag taxonomy.

## Resuming an Existing Wiki (CRITICAL — do this every session)

When the user has an existing wiki, **always orient yourself before doing anything**:

① **Read `SCHEMA.md`** — understand the domain, conventions, and tag taxonomy.
② **Read `index.md`** — learn what pages exist and their summaries. This help you find out the already parsed soure (if any)
③ **Scan recent `log.md`** — read the last 20-30 entries to understand recent activity.
④ **Check `raw/`** — if the source already exists in `raw/`, use that as the source of truth. Do **not** call web
tools again unless the user explicitly asks to refresh/re-ingest from the web.
⑤ **Inform the user** — briefly state what you found (existing raw file, related wiki pages) and your next steps before writing.
in

```bash
WIKI="${WIKI_PATH:-$(pwd)/wiki}"
# Orientation reads at session start
read_file "$WIKI/SCHEMA.md"
read_file "$WIKI/index.md"
read_file "$WIKI/log.md" offset=<last 30 lines>
```

Only after orientation should you ingest, query, or lint. This prevents:

- Creating duplicate pages for entities that already exist
- Missing cross-references to existing content
- Contradicting the schema's conventions
- Repeating work already logged

For large wikis (100+ pages), also run a quick `grep` (grep tool) for the topic at hand before creating anything new.

## Initializing a New Wiki

When the user asks to create or start a wiki:

1. Determine the wiki path (from `$WIKI_PATH` env var, or ask the user; default `WIKI="${WIKI_PATH:-$(pwd)/wiki}"`)
2. Create the directory structure above
3. Ask the user what domain the wiki covers — be specific
4. Write `SCHEMA.md` customized to the domain (see template below)
5. Write initial `index.md` with sectioned header
6. Write initial `log.md` with creation entry
7. Confirm the wiki is ready and suggest first sources to ingest

### SCHEMA.md Template

Adapt to the user's domain. The schema constrains agent behavior and ensures consistency:

```markdown
# Wiki Schema

## Domain
[What this wiki covers — e.g., "AI/ML research", "personal health", "startup intelligence"]

## Conventions
- File names: lowercase, hyphens, no spaces (e.g., `transformer-architecture.md`)
- Every wiki page starts with YAML frontmatter (see below)
- Use `[[wikilinks]]` to link between pages (minimum 2 outbound links per page)
- When updating a page, always bump the `updated` date
- Every new page must be added to `index.md` under the correct section
- Every action must be appended to `log.md`
- **Provenance markers:** On pages that synthesize 3+ sources, append `^[raw/articles/source-file.md]` at the end of paragraphs whose claims come from a specific source. This lets a reader trace each claim back without re-reading the whole raw file. Optional on single-source pages where the `sources:` frontmatter is enough.

## Frontmatter
  ```yaml
  ---
  title: Page Title
  created: YYYY-MM-DD
  updated: YYYY-MM-DD
  type: entity | concept | comparison | query | summary
  tags: [from taxonomy below]
  sources: [raw/papers/source-name.md]
  # Optinal for arxiv papers:
  authors: [Name1, Name2]
  # Optional quality signals:
  confidence: high | medium | low        # how well-supported the claims are
  contested: true                        # set when the page has unresolved contradictions
  contradictions: [other-page-slug]      # pages this one conflicts with
  ---
  ```

`confidence` and `contested` are optional but recommended for opinion-heavy or fast-moving
topics. Lint surfaces `contested: true` and `confidence: low` pages for review so weak claims
don't silently harden into accepted wiki fact.

### raw/ Frontmatter

Raw sources ALSO get a small frontmatter block so re-ingests can detect drift:

```yaml
---
source_url: https://example.com/article   # original URL, if applicable
ingested: YYYY-MM-DD
sha256: <hex digest of the raw content below the frontmatter>
---
```

The `sha256:` lets a future re-ingest of the same URL skip processing when content is unchanged,
and flag drift when it has changed. Compute over the body only (everything after the closing
`---`), not the frontmatter itself. Use `compute_sha256(text=<raw body>)`, which defaults to
the wiki convention `body.lstrip("\n")`. Do **not** use `execute`, `python -c`, or shell commands
to compute this hash.

## Tag Taxonomy

[Define 8-15 top-level tags for the domain. Add new tags here BEFORE using them.]

Example for AI/ML:

- Models: model, architecture, benchmark, training
- People/Orgs: person, company, lab, open-source
- Techniques: optimization, fine-tuning, inference, alignment, data
- Meta: comparison, timeline, controversy, prediction

Rule: every tag on a page must appear in this taxonomy. If a new tag is needed,
add it here first, then use it. This prevents tag sprawl.

## Page Thresholds

- **Create a page** when an entity/concept appears in 2+ sources OR is central to one source
- **Add to existing page** when a source mentions something already covered
- **DON'T create a page** for passing mentions, minor details, or things outside the domain
- **Split a page** when it exceeds ~200 lines — break into sub-topics with cross-links
- **Archive a page** when its content is fully superseded — move to `_archive/`, remove from index

### Hard Limits

- Max **4 concepts** created — pick the most central ones only
- For research paper, max **2 entities** - must have first author and organization (if any)

## Entity Pages

One page per notable entity. Include:

- Overview / what it is
- Key facts and dates
- Relationships to other entities ([[wikilinks]])
- Source references

## Concept Pages

One page per concept or topic. Include:

- Definition / explanation
- Current state of knowledge
- Open questions or debates
- Related concepts ([[wikilinks]])

## Comparison Pages

Side-by-side analyses. Include:

- What is being compared and why
- Dimensions of comparison (table format preferred)
- Verdict or synthesis
- Sources

## Update Policy

When new information conflicts with existing content:

1. Check the dates — newer sources generally supersede older ones
2. If genuinely contradictory, note both positions with dates and sources
3. Mark the contradiction in frontmatter: `contradictions: [page-name]`
4. Flag for user review in the lint report

### index.md Template

The index is sectioned by type. Each entry is one line: wikilink + summary.

```markdown
# Wiki Index

> Content catalog. Every wiki page listed under its type with a one-line summary.
> Read this first to find relevant pages for any query.
> Last updated: YYYY-MM-DD | Total pages: N

## Entities
<!-- Alphabetical within section -->

## Concepts

## Comparisons

## Queries
```

**Scaling rule:** When any section exceeds 50 entries, split it into sub-sections
by first letter or sub-domain. When the index exceeds 200 entries total, create
a `_meta/topic-map.md` that groups pages by theme for faster navigation.

### log.md Template

```markdown
# Wiki Log

> Chronological record of all wiki actions. Append-only.
> Format: `## [YYYY-MM-DD] action | subject`
> Actions: ingest, update, query, lint, create, archive, delete
> When this file exceeds 500 entries, rotate: rename to log-YYYY.md, start fresh.

## [YYYY-MM-DD] create | Wiki initialized
- Domain: [domain]
- Structure created with SCHEMA.md, index.md, log.md
```

## Core Operations

### 1. Ingest - only for ingesting new source - SKIP this if resuming/fixing existing wiki

① **Capture the raw source:**

**Research paper:**

- **Only if `fetch_arxiv` and `parse_pdf_docling` are available:** use them for any paper with an arXiv ID, arXiv URL, clear paper title, or URL that `fetch_arxiv` can resolve. Do **not** use web tools for those cases:
  - Call `fetch_arxiv(query)` with the arXiv ID, URL, or title → returns `pdf_path`, `title`, `metadata`
  - Call `parse_pdf_docling(pdf_path)` → returns clean markdown with preserved headings, tables, equations

- Otherwise, use web tools :
  - Direct paper URL/PDF URL → call `web_extract([url])`
  - Title or partial info → call `web_search(query)` at most **2 times** (preferably 1 only), then `web_extract([url])` on the best relevant result
  - If no relevant result is found after 2 searches, stop and inform the user instead of guessing

**Article, blog, docs, project page, news, Medium/Substack, or any non-paper web source:**

- If the user gave a direct URL → call `web_extract([url])` directly. If the result shows sign-in/payment is required, stop and inform user immediately.
- If the URL is missing or ambiguous → call `web_search(query)` first, then `web_extract([url])`.
- Save articles and general web sources to `raw/articles/<slug>.md`.

**Pasted text:**

- Save directly to `raw/articles/<slug>.md` (or `raw/papers/` if it's a paper excerpt)
- Derive a slug from the first heading or first line
- **After saving:** prepend the raw frontmatter block

**IMPORTANT**: Check the wiki before extracting. If `raw/papers/<slug>.md` or `raw/articles/<slug>.md` already exists, compare its sha256 against a fresh fetch — skip extraction entirely if content is unchanged.

② **Discuss takeaways** with the user — what's interesting, what matters.

③ **Check what already exists** — search index.md and use `grep` to find existing pages for mentioned entities/concepts e.g. `grep(pattern="attention mechanism", path="/wiki/", glob="**/*.md")`

This is the difference between a growing wiki and a pile of duplicates.

④ **Write or update wiki pages:**

- **New entities/concepts:** Create pages only if they meet the Page Thresholds
  in SCHEMA.md (2+ source mentions, or central to one source)
- **Existing pages:** Add new information, update facts, bump `updated` date.
  When new info contradicts existing content, follow the Update Policy.
- **Cross-reference:** Every new or updated page must link to at least 2 other
  pages via `[[wikilinks]]`. Check that existing pages link back.
- **Tags:** Only use tags from the taxonomy in SCHEMA.md
- **Provenance:** On pages synthesizing 3+ sources, append `^[raw/articles/source.md]`
  markers to paragraphs whose claims trace to a specific source.
- **Confidence:** For opinion-heavy, fast-moving, or single-source claims, set
  `confidence: medium` or `low` in frontmatter. Don't mark `high` unless the
  claim is well-supported across multiple sources.

⑤ **Update navigation:**

- Add new pages to `index.md` under the correct section, alphabetically
- Update the "Total pages" count and "Last updated" date in index header
- Append to `log.md`: `## [YYYY-MM-DD] ingest | Source Title`
- List every file created or updated in the log entry

⑥ **Quick check (default after ingest):** Run `quick_wiki_integrity_check` which only catches broken `[[wikilinks]]` and frontmatter/tag issues. This is the standard post-ingest validation; do not run the full health check unless the user explicitly asks for a lint/audit.

⑦ **Report what changed** — list every file created or updated to the user.

A single source can trigger updates across 5-15 wiki pages. This is normal
and desired — it's the compounding effect.

### 2. Query

When the user asks a question about the wiki's domain:

① **Read `index.md`** to identify relevant pages.
② **For wikis with 50+ pages**, also `grep` across all `.md` files
   for key terms — the index alone may miss relevant content.
③ **Read the relevant pages** using `read_file`.
④ **Synthesize an answer** from the compiled knowledge. Cite the wiki pages
   you drew from: "Based on [[page-a]] and [[page-b]]..."
⑤ **File valuable answers back** — if the answer is a substantial comparison,
   deep dive, or novel synthesis, create a page in `queries/` or `comparisons/`.
   Don't file trivial lookups — only answers that would be painful to re-derive.
⑥ **Update log.md** with the query and whether it was filed.

### 3. Lint - Full Health Check

Only run this section when the user explicitly asks to lint / health-check / audit the wiki.
For normal ingest flows, `quick_wiki_integrity_check` is enough (see Ingest step ⑥).

① Run `quick_wiki_integrity_check` tool which only checks:

- **Broken wikilinks:** use `files=None` (or no args) to scan wikilinks in all files
- **Frontmatter validation:** Every wiki page must have all required fields
   (title, created, updated, type, tags, sources). Tags must be in the taxonomy.

② **Orphan pages:** Find pages with no inbound `[[wikilinks]]` from other pages.

```python
# Use execute_code for this — programmatic scan across all wiki pages
import os, re
from collections import defaultdict
wiki = "<WIKI_PATH>"
# Scan all .md files in entities/, concepts/, comparisons/, queries/
# Extract all [[wikilinks]] — build inbound link map
# Pages with zero inbound links are orphans
```

③ **Index completeness:** Every wiki page should appear in `index.md`. Compare the filesystem against index entries.

④ **Stale content:** Pages whose `updated` date is >90 days older than the most
   recent source that mentions the same entities.

⑤ **Contradictions:** Pages on the same topic with conflicting claims. Look for
   pages that share tags/entities but state different facts. Surface all pages
   with `contested: true` or `contradictions:` frontmatter for user review.

⑥ **Quality signals:** List pages with `confidence: low` and any page that cites
   only a single source but has no confidence field set — these are candidates
   for either finding corroboration or demoting to `confidence: medium`.

⑦ **Source drift:** For each file in `raw/` with a `sha256:` frontmatter, recompute
   the hash and flag mismatches. Mismatches indicate the raw file was edited
   (shouldn't happen — raw/ is immutable) or ingested from a URL that has since
   changed. Not a hard error, but worth reporting.

⑧ **Page size:** Flag pages over 200 lines — candidates for splitting.

⑨ **Tag audit:** List all tags in use, flag any not in the SCHEMA.md taxonomy.

⑩ **Log rotation:** If log.md exceeds 500 entries, rotate it.

⑪ **Report findings** with specific file paths and suggested actions, grouped by
   severity (broken links > orphans > source drift > contested pages > stale content > style issues).

⑫ **Append to log.md:** `## [YYYY-MM-DD] lint | N issues found`

## Working with the Wiki

### Searching

Use the built-in filesystem tools:

```text
# Find pages by content
grep(pattern="transformer", path="/wiki/", glob="*.md")

# Find pages by filename
glob(pattern="**/*.md", path="/wiki/")

# Find pages by tag
grep(pattern="tags:.*alignment", path="/wiki/", glob="*.md")

# Recent activity — read_file has offset + limit, both are line numbers
# Step 1 — get total lines (read with high offset to find end)
read_file(file_path="/wiki/log.md")  # check total_lines from result

# Step 2 — read last 20
read_file(file_path="/wiki/log.md", offset=<total_lines - 20>, limit=20)
```

⑬ Update 2 files under `wiki/graph/`:

- `wiki/graph/graph.json`: node list (concepts, entities, comparisons, queries, summaries, and source-doc nodes) + directed edges between them (e.g. `introduces`, `uses`, `authored_by`) with `confidence: EXTRACTED|INFERRED`.

- `wiki/graph/citations.json`: per source-doc citation metadata (`title`, `authors`, `year`, optional `arxiv_id`) + `references` and `cited_by` lists (by source-doc id).

### Bulk Ingest

When ingesting multiple sources at once, batch the updates:

1. Read all sources first
2. Identify all entities and concepts across all sources
3. Check existing pages for all of them (one search pass, not N)
4. Create/update pages in one pass (avoids redundant updates)
5. Update index.md once at the end
6. Write a single log entry covering the batch

### Archiving

When content is fully superseded or the domain scope changes:

1. Create `_archive/` directory if it doesn't exist
2. Move the page to `_archive/` with its original path (e.g., `_archive/entities/old-page.md`)
3. Remove from `index.md`
4. Update any pages that linked to it — replace wikilink with plain text + "(archived)"
5. Log the archive action

## Pitfalls

- **Never modify files in `raw/`** — sources are immutable. Corrections go in wiki pages.
- **Always orient first** — read SCHEMA + index + recent log before any operation in a new session.
  Skipping this causes duplicates and missed cross-references.
- **Always update index.md, log.md and wiki/graph/** — skipping this makes the wiki degrade. These are the navigational backbone.
- **Don't create pages for passing mentions** — follow the Page Thresholds in SCHEMA.md. A name
  appearing once in a footnote doesn't warrant an entity page.
- **Don't create pages without cross-references** — isolated pages are invisible. Every page must link to at least 2 other pages.
- **Frontmatter is required** — it enables search, filtering, and staleness detection.
- **Tags must come from the taxonomy** — freeform tags decay into noise. Add new tags to SCHEMA.md first, then use them.
- **Keep pages scannable** — a wiki page should be readable in 30 seconds. Split pages over 200 lines. Move detailed analysis to dedicated deep-dive pages.
- **Ask before mass-updating** — if an ingest would touch 5+ existing pages, confirm the scope with the user first.
- **Rotate the log** — when log.md exceeds 500 entries, rename it `log-YYYY.md` and start fresh.
  The agent should check log size during lint.
- **Handle contradictions explicitly** — don't silently overwrite. Note both claims with dates, mark in frontmatter, flag for user review.
- **Remember to add sha256**
- **Avoid using web tools** for resuming and existing wiki unless user explicitly ask you to.
