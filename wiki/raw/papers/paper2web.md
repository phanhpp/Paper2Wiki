---
source_url: https://arxiv.org/html/2510.15842v1
ingested: 2026-06-01
sha256: 7cd1e0306f2b68c5426dac3f8f92a85dfb8a845
---

# Paper2Web: Let's Make Your Paper Alive!

**arXiv:2510.15842v1 [cs.CL] | October 17, 2025**

**License:** CC BY 4.0

---

## Executive Summary

Paper2Web introduces a comprehensive benchmark and autonomous pipeline for converting academic papers into interactive, multimedia-rich project homepages. The work addresses critical limitations in current approaches (direct LLM generation, templates, HTML conversion) that struggle with layout awareness and interactivity. **PWAgent**, the proposed solution, achieves state-of-the-art results while maintaining 82% cost reduction compared to end-to-end baselines.

**Key Resources:**
- Project Website: https://francischen3.github.io/P2W_Website
- Code Repository: https://github.com/YuhangChen1/Paper2All

## Problem Statement

### Current Limitations

**PDF Format Constraints:**
- Static text and images only
- Limited interactivity and multimedia support
- Substantial information loss during dissemination

**Existing Approaches Fail Because:**
- **arXiv HTML**: Produces disordered layouts, redundant text, rigid figure grids with inconsistent scaling, detached captions, missing responsiveness
- **AlphaXiv**: Limited author control over multimedia placement, largely static presentations
- **Direct LLM Generation**: Struggles with long contexts and effective multimedia integration

**The Gap:** Formats that preserve core textual knowledge while seamlessly integrating multimedia for diverse communities remain absent.

## Paper2Web Dataset

### Data Collection Pipeline

**Paper Metadata:**
- Collected from major AI conferences (ICML, NeurIPS, WWW, ICLR, etc., 2020-2025)
- Extracted: title, authors, venue, year, citation counts (Semantic Scholar)
- LLM-assigned topical categories: 13 categories covering diverse AI subfields

**Project Website Search:**
- Retrieved external links from paper body and code repositories
- LLM analysis of content relevance
- Human review for ambiguous cases

### Dataset Characteristics

**Scale:** 10,716 papers with verified homepages + 85,843 without

**Feature Distribution:**
- Static sites (text and still images)
- Multimedia sites (embedded videos and animations)
- Interactive sites (dynamic behaviors and explorable components)

## Evaluation Framework

### Three-Dimensional Benchmark

#### 1. Connectivity & Completeness

**Connectivity Metrics:**
- External links: Valid, reachable, contextually relevant URLs
- Internal navigation: Anchor links referencing local sections

**Completeness Metrics:**
- Image-Text Balance Prior: Penalizes deviation from ideal 1:1 ratio
- Information Efficiency Prior: Rewards concise, information-dense presentation

#### 2. Holistic MLLM-as-a-Judge Evaluation

**Three Dimensions (1-5 scale):**
- **Interactive**: Element responsiveness, saliency emphasis, usability
- **Aesthetic**: Element quality, layout balance, visual appeal
- **Informative**: Content clarity, logical coherence

#### 3. PaperQuiz: Knowledge Transfer Assessment

**Question Types (50 total):**
- Verbatim (25): Directly answerable from webpage text/figures/tables
- Interpretive (25): Requires high-level comprehension of contributions, methodology, results

## PWAgent: Multi-Agent Framework

### Architecture Overview

Three-stage pipeline with iterative refinement:

#### Stage 1: Paper Decomposition

1. Convert PDF to Markdown (MARKER or DOCLING)
2. LLM semantic decomposition against predefined schema
3. Extract and organize three asset categories:
   - Textual Assets: Logical sections with title, synopsis, full text, metadata
   - Visual Assets: Figures/tables as images linked to captions, labels
   - Link Assets: External URLs and internal citations

#### Stage 2: MCP Ingestion

**Model Context Protocol (MCP) Server:**
- Converts static assets into queryable resources with stable IDs
- Enriches with cross-modal semantics
- Link assets typed by function for structured cross-references
- Content-aware spatial allocation heuristic estimates asset footprint

#### Stage 3: Agent-Driven Iterative Refinement

- MLLM as Orchestrator Agent conducts holistic visual assessments
- Segments rendered page into visual tiles linked to HTML fragments
- Sequential analysis of each tile to detect imbalances
- Adjacent tiles merged for joint optimization
- Global pass to assess completeness and visual harmony
- Terminates when optimization complete or iteration limit reached

## Experimental Results

### Baselines Evaluated

1. Oracle Method (author-created websites)
2. End-to-End Generation (GPT-4o, Gemini, DeepSeek, Qwen)
3. Template-Based methods (with Nerfies template)
4. Existing HTML Versions (arXiv HTML, alphaXiv)
5. PWAgent (proposed MCP-based approach)

### Main Findings

**Completeness & Connectivity:**
- arXiv-HTML: High rule-based connectivity but 64% lower human ratings
- PWAgent: 2% higher LLM-judged completeness than ground truth
- Superior content condensation and balanced layout

**Holistic Evaluation:**
- PWAgent achieves highest scores across all dimensions
- 91% of ground truth quality in aesthetics
- 94% in informativeness
- 59% improvement in interactivity over alphaXiv

**PaperQuiz Knowledge Transfer:**
- PWAgent achieves best or near-best results across tasks
- Total information coverage rivals arXiv-HTML
- With verbosity penalty, still attains highest overall score

**Cost Efficiency:**
- PWAgent: $0.025 per website
- GPT-4o: ~$0.141 per website (82% cost reduction)
- Gemini: ~$0.054 per website (54% cost reduction)

## Key Contributions

1. **New Task, Dataset, and Evaluation Suite**: Paper2Web dataset linking 10,716 scientific papers to project homepages
2. **Comprehensive Benchmark**: Multi-dimensional evaluation (Connectivity, Completeness, MLLM-as-Judge, PaperQuiz)
3. **State-of-the-Art Approach**: PWAgent achieves superior quality with 82% cost reduction; operates on Pareto frontier

## Related Work

### HTML Code Generation
- Automated front-end development (Design2Code, Websight, WebCode2M)
- Divide-and-conquer strategies (DCGen) and hierarchical generation (UICopilot)
- Multi-agent systems for complex development tasks

### Automated Processing of Scholarly Articles
- Template-based and rule-driven models (paper-to-poster, slides, videos)
- Recent agentic approaches: Paper2Poster, PresentAgent, Paper2Video
- MCP-enabled systems: Paper2Agent

## Conclusion

Paper2Web defines an emerging task for transforming academic papers into interactive homepages. PWAgent narrows the gap between machine- and human-designed websites. Future work should integrate multi-agent reasoning, multimodal understanding, and advance agentic workflows for scholarly communication beyond static formats.
