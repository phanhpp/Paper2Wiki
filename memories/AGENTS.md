# AGENTS.md — Paper2Wiki Conventions

## Wiki Structure
- Papers: /wiki/papers/<slug>.md
- Concepts: /wiki/concepts/<slug>.md  
- Entities: /wiki/entities/<slug>.md
- Graph: /wiki/graph/citations.json, graph.json
- Index: /wiki/index.md (catalog, update on every ingest)
- Log: /wiki/log.md (append-only, never edit past entries)

## Slug Convention
- lowercase_with_underscores
- derived from title: "Attention Is All You Need" → attention_is_all_you_need
- must match filename exactly

## Frontmatter
- Every page must have valid YAML frontmatter
- slug field must match filename without .md
- date format: YYYY-MM-DD

## Wikilinks
- Only link to pages that exist or you are creating this session
- Never create broken wikilinks
- Forward references to uningested papers → plain text only