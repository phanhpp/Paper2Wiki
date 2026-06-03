---
title: Paper2Web Benchmark & Evaluation Framework
created: 2026-06-01
updated: 2026-06-01
type: comparison
tags: [benchmark, survey, comparison]
sources: [raw/papers/paper2web.md]
confidence: high
---

# Paper2Web Benchmark & Evaluation Framework

## Overview

The **Paper2Web benchmark** establishes the first comprehensive evaluation suite for academic webpage generation. It combines rule-based metrics, human-verified MLLM judgments, and knowledge transfer tests to measure quality across multiple dimensions.

## Dataset

- **10,716 papers** with verified human-created project homepages (gold standard)
- **85,843 papers** without homepages (negative examples)
- **13 subject categories** (following ICML/NeurIPS taxonomies)
- **2,000 manual audits** of website features (static, multimedia, interactive)
- **6 human annotators** with diverse backgrounds for rating validation

## Three-Dimensional Evaluation Framework

### 1. Connectivity & Completeness (Rule-Based)

**Connectivity Score**: Measures link quality and navigability.

- **External Links** (Sext): Valid, reachable, contextually relevant outbound URLs
  - Parsed via HTML parser; verified by URL checker
- **Internal Navigation** (Sint): Anchor links (href="#section-id") for jump-to-section
- **Formula**: S_Con = (Sext + Sint) / 2

**Completeness Metrics**: Measures content balance and information density.

#### Image-Text Balance Prior
Penalizes deviation from ideal 1:1 image-to-text ratio:
- **Weighted deviation** D computed across page containers
- **Penalty term**: ζ = 5 / (1 + γ·D)
- **Score**: S_img-txt = 5 - ζ
- **Scaling factor**: γ > 0 (adjustable per domain)

**Insight**: Avoids both text-dense "wall-of-text" and image-sparse layouts.

#### Information Efficiency Prior
Rewards concise, information-dense presentation:
- Let r = L/W = (generated text length) / (median human-designed length)
- **Efficiency**: p(r) = 5 / (1 + β·max(0, r-1))
- **Scaling factor**: β = 0.6 (typical)
- **Score**: S_comp = (S_img-txt + p(r)) / 2

**Insight**: Penalizes verbose pages that merely copy the paper; rewards synthesis.

### 2. Holistic MLLM-as-a-Judge (1-5 Scale)

Human-verified MLLM evaluation across three dimensions:

#### Interactive (Element Responsiveness)
- Hover effects, expand/collapse sections
- Interactive visualizations (sliders, explorable charts)
- Live demos or embedded interactive components
- Navigation aids (floating TOC, jump-to-section, back-to-top)

#### Aesthetic (Visual Design)
- Element quality (clarity, resolution, design thoughtfulness)
- Layout balance (alignment, sizing, spacing)
- Engagement and style (color harmony, typography, creativity)
- Clarity (legibility, lack of clutter, visual hierarchy)

#### Informative (Content & Structure)
- Logical flow and coherence (narrative structure mirrors research process)
- Completeness and depth (all major findings present)
- Scannability (effective use of headings, bullet points, callouts)
- Information architecture (clear labels, cross-links, searchability)

**Validation**: 6 independent human raters (diverse background) cross-validate MLLM scores to mitigate bias.

### 3. PaperQuiz: Knowledge Transfer Assessment

Tests whether readers can understand the paper *from the webpage alone*.

#### Protocol
1. **Question generation**: LLM generates 50 questions from source paper:
   - **Verbatim (25)**: Directly answerable from webpage text, figures, or tables
   - **Interpretive (25)**: Requires high-level comprehension of contributions, methods, results

2. **Answering**: Multiple MLLMs (3 open-source, 3 closed-source) answer based on *rendered webpage screenshots only*

3. **Scoring**: Compare answers against ground truth; measure knowledge transfer

4. **Verbosity Penalty**: Discount high scores achieved via text-heavy layouts
   - Penalty term: ζ (from Eq. 1 above)
   - Final score: S_quiz = (verbatim_score + interpretive_score) / 2 - penalty

**Insight**: Differentiates between "cram all text" (scores well without penalty) and "summarize effectively" (maintains score post-penalty).

## Benchmark Results (PWAgent vs. Baselines)

### Connectivity & Completeness

| Method | Connectivity ↑ | Completeness ↑ | Combined |
|--------|---|---|---|
| arXiv HTML | 3.70 (high) | 2.42 | 3.06 |
| alphaXiv | 3.43 | 3.12 | 3.28 |
| **PWAgent** | **3.06** | **3.10** | **3.08** |
| **Oracle** | 3.20 | 3.22 | 3.21 |

**Insight**: PWAgent's lower rule-based connectivity reflects *selective* linking (quality over quantity); higher human ratings validate this design choice.

### Holistic MLLM-as-a-Judge

| Method | Interactive ↑ | Aesthetic ↑ | Informative ↑ |
|--------|---|---|---|
| arXiv HTML | 1.05 | 2.72 | 4.01 |
| alphaXiv | 1.25 | 2.73 | 4.20 |
| **PWAgent** | **1.39** | **3.35** | **4.31** |
| **Oracle** | 1.70 | 3.14 | 4.49 |

**Key Finding**: PWAgent achieves 91% of oracle aesthetic quality and 94% of informativeness, with **59% improvement in interactivity** over alphaXiv.

### PaperQuiz Knowledge Transfer

| Method | Verbatim ↑ | Interpretive ↑ | Penalty ↓ | Final ↑ |
|--------|---|---|---|---|
| arXiv HTML | 3.62 | 4.52 | 2.87 | 1.13 |
| alphaXiv | 3.57 | 4.58 | 1.97 | 2.10 |
| **PWAgent** | **3.76** | **4.56** | **2.00** | **2.03** |
| **Oracle** | 2.94 | 3.81 | 1.43 | 1.57 |

**Key Finding**: PWAgent achieves highest knowledge transfer among generated methods while maintaining competitive verbosity (similar penalty to oracle).

### Cost Efficiency

**Generation cost per website:**
- PWAgent: **$0.025** (baseline)
- GPT-4o end-to-end: $0.141 (5.6× more expensive)
- Gemini-2.5 end-to-end: $0.054 (2.2× more expensive)
- GPT-4o + template: $0.069 (2.8× more expensive)

**PWAgent Cost Advantage**: 82% reduction vs. GPT-4o; 54% vs. Gemini

## Limitations & Interpretation

### arXiv HTML Paradox
- Highest rule-based connectivity (3.70) but **64% lower human ratings** on same metric
- Reason: Indiscriminately converts *every* citation into a link, inflating metrics while degrading UX
- **Lesson**: Code-based metrics can misalign with human preference; human validation essential

### PaperQuiz Interpretation
- Ground truth (oracle) scores *lower than expected* on PaperQuiz
- Likely cause: Many real project pages emphasize videos, animations, or interactive demos—not visible in screenshots
- **Implication**: Authors can start with PWAgent output and add multimedia to reach ideal design

### Evaluation Gaps
- Doesn't measure how well multimedia *contributes* to understanding
- Doesn't evaluate accessibility (keyboard navigation, screen reader compatibility)
- Limited to text-based webpage content; excludes embedded demos or live services

## Comparison to Related Benchmarks

| Benchmark | Domain | Metrics | Dataset Size |
|-----------|--------|---------|---|
| Design2Code | screenshot → HTML | Visual similarity, code correctness | ~1000 examples |
| Websight | screenshot → HTML | Structure, layout, functionality | ~10K screenshots |
| **Paper2Web** | **paper → website** | **Connectivity, Completeness, MLLM-Judge, PaperQuiz** | **~10K papers** |

---

**Uniqueness**: Paper2Web is the first benchmark combining rule-based metrics, human-verified MLLM judgment, and knowledge transfer assessment—tailored specifically to academic webpage quality.

**Related Pages:** [[paper-to-web-conversion]], [[agent-based-webpage-generation]]

**Sources:** ^[raw/papers/paper2web.md]
