---
name: lint-fix
description: Fix broken wikilinks and frontmatter errors in existing wiki pages. Use when lint_check() reports errors or user asks to fix lint.
---

# Lint-Fix Skill

**Do NOT create new pages speculatively. Fix by replacing broken links first.**

---

## Tools

- `lint_check(files=None)` → returns errors/warnings. `files=None` = full wiki
- Built-in filesystem tools: `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`

---

## Stub Page Decision

A stub page is a minimal page (frontmatter + one-paragraph definition) created to 
resolve a broken wikilink without doing a full ingest.

Before creating any stubs:
1. Read `/wiki/index.md` and `/wiki/graph/graph.json`
2. For each broken `[[slug]]`, assess importance:
   - How many existing pages link to it? (grep `/wiki/` for `[[slug]]`)
   - Is it a node in graph.json with edges? (central concept)
   - Is it a paper slug? (never stub — ingest properly or plain text)
3. Present assessment to user:

"Found 5 broken wikilinks:
- [[attention_mechanism]] — linked from 4 pages, central node in graph → recommend stub
- [[encoder_decoder]] — linked from 2 pages, in graph → recommend stub  
- [[bert]] — paper not yet ingested → recommend plain text
- [[rotary_position_embedding]] — linked from 1 page, not in graph → recommend plain text
- [[alibi]] — linked from 1 page, not in graph → recommend plain text

Create stubs for attention_mechanism and encoder_decoder, replace the rest with plain text?
Or would you like to handle any differently?"

4. Wait for user confirmation before proceeding
5. Execute based on user decision

---

## Workflow

1. Run `lint_check()` — categorize errors into:
   - Broken wikilinks → handle per Broken Links section
   - Frontmatter errors → handle per Frontmatter Errors section
2. Fix broken links first (they may cascade)
3. Fix frontmatter errors
4. Re-run `lint_check()` — repeat until `lint: OK`
5. If errors persist after 3 iterations → report to user, stop looping


---

## Frontmatter Errors

For `missing frontmatter block` errors:
- Read the file first to understand its content and type
- Add correct frontmatter using the schema below
- Never guess the slug — derive it from the filename

For `missing required fields` errors:
- Add only the missing fields, do not touch existing content

Required schema:
```yaml
---
type: paper | concept | entity
title: "Human Readable Title"
date: YYYY-MM-DD
slug: filename_without_extension
source_count: 1
confidence: high | medium | low
tags: []
---
```
For `type: paper` also add: `authors: []` and `arxiv_id: ""`

---
## After Fixing

If all check ok:
- update log.md with 'lint check with [scope - either files or whole wiki]: ok'

If you only replaced broken links with plain text:
- No index.md or graph.json updates needed
- update log.md to record what you have fixed

If you created stub pages:
- Update `/wiki/index.md` — add stub entries
- Update `/wiki/graph/graph.json` — add new nodes
- Append to `/wiki/log.md`:

```markdown
## [YYYY-MM-DD] lint-fix | <description>
Fixed: replaced [[bert]], [[gpt]] with plain text
Created stubs: attention_mechanism.md, encoder_decoder.md
Lint: passed
```