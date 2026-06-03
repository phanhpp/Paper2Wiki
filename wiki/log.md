# Wiki Log

> Chronological record of all wiki actions. Append-only.
> Format: `## [YYYY-MM-DD] action | subject`
> Actions: ingest, update, query, lint, create, archive, delete
> When this file exceeds 500 entries, rotate: rename to log-YYYY.md, start fresh.

## [2026-06-01] ingest | Paper2Web: Let's Make Your Paper Alive! (Chen et al., 2025)
- Source: https://arxiv.org/html/2510.15842v1 (arXiv: 2510.15842)
- Raw frontmatter stamped: raw/papers/paper2web.md (sha256: 7cd1e030...)
- Created concepts/paper-to-web-conversion.md
- Created concepts/agent-based-webpage-generation.md
- Created concepts/mcp-model-context-protocol.md
- Created comparisons/paper2web-benchmark.md
- Updated index.md (total pages: 7 → 11)
- Wiki integrity check: PASS

## [2025-01-27] create | Wiki initialized
- Domain: ML/AI Research
- Structure created with SCHEMA.md, index.md, log.md

## [2025-01-27] ingest | Attention Is All You Need (Vaswani et al., 2017)
- Source: raw/papers/attention_is_all_you_need.pdf (arxiv: 1706.03762)
- Raw frontmatter stamped: raw/assets/attention_is_all_you_need/attention_is_all_you_need.md (sha256: 09410142...)
- Created concepts/transformer-architecture.md
- Created concepts/self-attention.md
- Created concepts/multi-head-attention.md
- Created concepts/scaled-dot-product-attention.md
- Created concepts/positional-encoding.md
- Created entities/ashish-vaswani.md
- Created entities/google-brain.md
- Updated index.md (7 pages total)
- Updated graph/graph.json and graph/citations.json

## [2025-07-14] lint | 0 issues found
- Checks run: wikilinks, frontmatter, orphans, index completeness, stale content, contradictions, quality signals, source drift, page sizes, tag audit, log rotation
- quick_wiki_integrity_check: OK
- Orphans: none (all 7 pages have ≥2 inbound links)
- Index completeness: OK (7/7 pages listed)
- Stale content: none (wiki is 1 day old, single source)
- Contradictions / contested pages: none
- Quality signals: all pages confidence: high, no missing confidence fields
- Source drift: sha256 verified OK (raw/assets/attention_is_all_you_need/attention_is_all_you_need.md — stored hash matches body.lstrip('\n'))
- Page sizes: all under 200 lines (max: transformer-architecture.md at 92 lines)
- Tag audit: 6 tags in use (architecture, company, lab, model, person, training) — all in taxonomy
- Log rotation: 2 entries (far below 500 threshold)
- Note: sha256 re-computation requires lstrip('\n') on body to match stored value — document this in SCHEMA.md
