# Wiki Schema

## Domain

**ML/AI Research** — This wiki covers machine learning and artificial intelligence research, including:
- Deep learning architectures and techniques
- Large language models (LLMs) and foundation models
- Training, fine-tuning, and inference methods
- Alignment, safety, and evaluation
- Key researchers, labs, and organizations
- Benchmarks and datasets
- Historical milestones and emerging trends

## Conventions

- File names: lowercase, hyphens, no spaces (e.g., `transformer-architecture.md`)
- Every wiki page starts with YAML frontmatter (see below)
- Use `[[wikilinks]]` to link between pages (minimum 2 outbound links per page)
- When updating a page, always bump the `updated` date
- Every new page must be added to `index.md` under the correct section
- Every action must be appended to `log.md`
- **Provenance markers:** On pages that synthesize 3+ sources, append `^[raw/articles/source-file.md]` at the end of paragraphs whose claims come from a specific source.

## Frontmatter

```yaml
---
title: Page Title
created: YYYY-MM-DD
updated: YYYY-MM-DD
type: entity | concept | comparison | query | summary
tags: [from taxonomy below]
sources: [raw/papers/source-name.pdf]
# Optional for arxiv papers:
authors: [Name1, Name2]
# Optional quality signals:
confidence: high | medium | low
contested: true
contradictions: [other-page-slug]
---
```

### raw/ Frontmatter

```yaml
---
source_url: https://example.com/article
ingested: YYYY-MM-DD
sha256: <hex digest of body content>
# Computed as: hashlib.sha256(body.lstrip('\n').encode('utf-8')).hexdigest()
# where body = everything after the closing --- delimiter
---
```

## Tag Taxonomy

### Models & Architectures
- `model` — a specific ML model or model family (e.g., GPT-4, LLaMA)
- `architecture` — neural network design patterns (e.g., Transformer, MoE)
- `benchmark` — evaluation suites and leaderboards
- `dataset` — training or evaluation datasets

### Techniques
- `training` — pretraining, optimization, learning rate schedules
- `fine-tuning` — RLHF, SFT, LoRA, instruction tuning
- `inference` — decoding strategies, quantization, serving
- `alignment` — safety, RLHF, Constitutional AI, red-teaming
- `data` — data curation, tokenization, synthetic data

### People & Orgs
- `person` — individual researchers or practitioners
- `company` — commercial AI organizations
- `lab` — research labs (academic or independent)
- `open-source` — open-source projects and communities

### Meta
- `comparison` — side-by-side analyses of models or techniques
- `timeline` — historical milestones and chronology
- `controversy` — disputed claims or ongoing debates
- `prediction` — forecasts about future AI developments
- `survey` — overview papers covering a broad area

**Rule:** Every tag on a page must appear in this taxonomy. Add new tags here first, then use them.

## Page Thresholds

- **Create a page** when an entity/concept appears in 2+ sources OR is central to one source
- **Add to existing page** when a source mentions something already covered
- **DON'T create a page** for passing mentions, minor details, or things outside the domain
- **Split a page** when it exceeds ~200 lines — break into sub-topics with cross-links
- **Archive a page** when its content is fully superseded — move to `_archive/`, remove from index

## Entity Pages

One page per notable entity (model, person, org, dataset, benchmark). Include:
- Overview / what it is
- Key facts and dates
- Relationships to other entities ([[wikilinks]])
- Source references

## Concept Pages

One page per concept or technique. Include:
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
