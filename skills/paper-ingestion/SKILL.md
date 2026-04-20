---
name: paper-ingestion
description: Fetch a paper from arXiv (by ID, URL, or topic), parse PDF text/tables/formulas/figures, and create structured wiki pages. Use for full paper ingestion only.
---

# Paper Ingestion Skill

Accepted inputs:
- arXiv ID: `1706.03762`
- arXiv URL: `https://arxiv.org/abs/1706.03762`
- Topic name: `attention is all you need`

---

## Scope Limits

- Max **5 concept pages** created — pick the most central ones only
- Max **8 entity pages** created — authors + main institutions only
- Total new pages per ingest: **≤ 15**
- Before creating any concept page ask: "Does this paper introduce or heavily explain this concept, or just reference it?"
  - Paper introduces/explains it → create page
  - Paper merely references it (bert, gpt, t5) → plain text only, no `[[wikilink]]`

### Concept priority (create in order, stop at limit):
1. Concepts this paper directly introduces or defines
2. Concepts this paper heavily relies on and explains in detail
3. Everything else → plain text mention, no wikilink

### Entity priority:
1. All authors of this paper
2. Primary affiliated institutions
3. Everything else → plain text, no wikilink

---

## Wikilink Rules

- Only use `[[slug]]` for pages that **already exist** or that **you are creating in this session**
- If a page doesn't exist and you're not creating it → use plain text, NOT a wikilink
- Forward references to other papers → plain text: `"BERT (Devlin et al. 2018)"` not `[[bert]]`
- Never create a wikilink you know will be broken

---

## Tools

- `fetch_arxiv(query)` → downloads PDF to `/raw/papers/<slug>.pdf`, returns metadata
- `parse_pdf_docling(pdf_path)` → returns `slug`, `title`, `page_count`, `body_chars`, `table_blocks`, `markdown_path`, `images_dir`, `images`, `tables_dir`, `table_images`
- Built-in filesystem tools: `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`
- `lint_check(files=None)` → runs lint, returns errors/warnings. `files=None` = full wiki

---

## YAML Frontmatter Schema

Every wiki page must start with:

```yaml
---
type: paper | concept | entity | comparison | synthesis
title: Human readable title
date: YYYY-MM-DD
slug: lowercase_with_underscores
source_count: N
confidence: high | medium | low
tags: [tag1, tag2]
---
```

For `type: paper`, also include:
```yaml
authors: [Name1, Name2]
arxiv_id: "1706.03762"
```

For full page templates → see `references/page-templates.md`

---

## Ingest Workflow

Before writing anything, make a plan:

1. Check what already exists:
   - `ls /raw/papers/` — is the PDF already downloaded?
   - `ls /raw/assets/` — are parsed assets already there?
   - `ls /wiki/papers/` — does the wiki page already exist?
2. List the ≤5 concepts you will create pages for
3. List the ≤8 entities you will create pages for
4. List everything else that will be plain text only

Then execute (skip steps that are already done):

1. **Fetch** — `fetch_arxiv(query)` → skip if PDF already exists in `/raw/papers/`
2. **Parse** — `parse_pdf_docling(pdf_path)` → skip if assets already exist in `/raw/assets/<slug>/`
3. **Read raw text** — `read_file` on `markdown_path`
4. **Write paper page** — `/wiki/papers/<slug>.md`
5. **Write concept pages** — only the ones in your plan
6. **Write entity pages** — only the ones in your plan
7. **Update graph** — `/wiki/graph/citations.json` and `/wiki/graph/graph.json`
8. **Update `/wiki/index.md`**
9. **Append to `/wiki/log.md`**
10. **Lint loop**

---

## graph.json Format

see `references/graph-format.md`

---

## index.md Format

```markdown
## Papers
- [[attention_is_all_you_need]] — Vaswani et al. 2017. Transformer architecture.

## Concepts
- [[attention_mechanism]] — Core building block of transformers. Used in 4 papers.

## Entities
- [[ashish_vaswani]] — Researcher at Google Brain. Lead author of transformer paper.
```

---

## log.md Format

Append-only. Never edit past entries.

```markdown
## [2026-04-18] ingest | Attention Is All You Need
Created: wiki/papers/attention_is_all_you_need.md
Updated: wiki/concepts/attention_mechanism.md, wiki/index.md
New concepts: attention_mechanism, positional_encoding, multi_head_attention
New entities: ashish_vaswani, google_brain
Lint: passed
```

---

## Lint Loop

After writing all pages, run `lint_check()`.
Fix only errors from pages YOU created in this session.
For broken links in other pre-existing pages → report to user and ask if they want run lint-fix separately.
Re-run until clean.

---

## Completion Checklist

- [ ] `/wiki/papers/<slug>.md` created with valid frontmatter
- [ ] All `[[wikilinks]]` point to existing or newly-created pages (no broken links)
- [ ] ≤5 concept pages created
- [ ] ≤8 entity pages created
- [ ] `/wiki/graph/citations.json` updated
- [ ] `/wiki/graph/graph.json` updated
- [ ] `/wiki/index.md` updated
- [ ] `/wiki/log.md` appended
- [ ] `lint_check()` returns 0 errors

---

## What This Skill Does NOT Do

- Fix lint errors in pre-existing pages — use the lint-fix skill
- Create comparison or synthesis pages — query-time only
- Modify other paper pages — only update shared concepts/entities
- Create pages for papers not yet ingested into this wiki
