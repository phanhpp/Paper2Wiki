# Page Templates

## Paper Page (`/wiki/papers/<slug>.md`)

```markdown
---
type: paper
title: "Attention Is All You Need"
date: 2026-04-18
slug: attention_is_all_you_need
authors: [Ashish Vaswani, Noam Shazeer, Niki Parmar]
arxiv_id: "1706.03762"
source_count: 1
confidence: high
tags: [transformers, attention, nlp]
---

# Attention Is All You Need (Vaswani et al., 2017)

## Core Contribution
One sentence: what this paper fundamentally proposes or proves.

## Methodology
How they did it. Key technical details worth preserving.

## Key Concepts
- [[attention_mechanism]] — brief note on how it's used here
- [[positional_encoding]] — brief note
- [[multi_head_attention]] — brief note

## Claims
- Claim 1 (supported / refuted by [[paper_slug]])

## Results
Key numbers, benchmarks, comparisons. Quote exactly from paper.

## Limitations
What the authors acknowledge or what later work revealed.

## Authors
- [[ashish_vaswani]] ([[google_brain]])

## Cited By
<!-- populated later as other papers are ingested -->

## Source
`raw/papers/attention_is_all_you_need.pdf`
```

---

## Concept Page (`/wiki/concepts/<slug>.md`)

```markdown
---
type: concept
title: "Attention Mechanism"
date: 2026-04-18
slug: attention_mechanism
source_count: 1
confidence: high
tags: [attention, transformers]
---

# Attention Mechanism

## Definition
What it is, in plain language.

## First Introduced
[[paper_that_introduced_it]] (year)

## Variations
- Scaled dot-product: [[attention_is_all_you_need]]

## Key Formula
If relevant, include the core equation.

## Related Concepts
- [[multi_head_attention]]
- [[positional_encoding]]
```

---

## Entity Page (`/wiki/entities/<slug>.md`)

```markdown
---
type: entity
title: "Ashish Vaswani"
date: 2026-04-18
slug: ashish_vaswani
source_count: 1
confidence: high
tags: [researcher, google-brain]
---

# Ashish Vaswani

## Affiliation
Google Brain (at time of publication)

## Papers in Wiki
- [[attention_is_all_you_need]] (2017)

## Key Contributions
Brief note on what this person is known for in this wiki's domain.
```
