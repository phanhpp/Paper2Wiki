---
title: Agent-Based Webpage Generation
created: 2026-06-01
updated: 2026-06-01
type: concept
tags: [inference, fine-tuning, alignment]
sources: [raw/papers/paper2web.md]
confidence: high
---

# Agent-Based Webpage Generation

**Definition:** Using multi-agent LLM systems (especially orchestrator agents with visual perception) to autonomously convert unstructured source documents (papers, designs) into structured, interactive web pages through iterative refinement.

## Key Insight

Unlike direct end-to-end generation (LLM → HTML), agent-based approaches decompose the task into:
1. **Parsing**: Structure the source (extract textual, visual, and link assets)
2. **Reasoning**: Semantically align assets and plan layout
3. **Synthesis**: Draft initial webpage
4. **Refinement**: Iteratively inspect rendered output and fix issues via tool calls

This staged approach reduces hallucinations, improves cost efficiency, and enables targeted fixes.

## Architecture Pattern

### Three-Stage Pipeline (PWAgent Example)

#### Stage 1: Paper Decomposition
- Convert PDF → Markdown (DOCLING, MARKER)
- LLM analyzes structure against predefined schema
- Extract **three asset types**:
  - **Textual Assets**: Sections with title, synopsis, full text, metadata
  - **Visual Assets**: Figures/tables with captions and backlinks
  - **Link Assets**: External URLs and citations, typed by function

#### Stage 2: MCP Ingestion
- Store decomposed assets in [[mcp-model-context-protocol]] server
- Enrich with cross-modal semantics (link visuals to text descriptions)
- Compute spatial allocation heuristics (estimate asset footprints)
- Expose via standardized tools for downstream access

#### Stage 3: Agent-Driven Iterative Refinement
- **Orchestrator Agent** (MLLM) assesses rendered page globally
- Segment page into independent visual tiles → identify issues locally
- Invoke MCP tools to edit HTML (fix layouts, balance text/images, adjust positioning)
- Merge adjacent tiles for joint optimization (resolve inter-section dependencies)
- Global pass to ensure completeness and harmony
- Loop until convergence or iteration limit

## Key Design Principles

### Joint Global-Local Reasoning
- **Global**: Assess page-level balance, informativeness, aesthetic appeal
- **Local**: Segment into independent regions, debug each in isolation
- **Merge**: Rejoin for cross-section coherence (prevents visual artifacts)

### Grounded Tool Use
- Link rendered screenshots to corresponding HTML fragments
- Tool calls target specific edits (not full page regeneration)
- Reduces hallucinations and improves convergence

### Cost Efficiency
- Lightweight MCP server replaces heavy end-to-end models
- Fewer LLM calls through structured decomposition
- ~82% cost reduction vs. GPT-4o end-to-end

## Advantages over Alternatives

| Approach | Strengths | Weaknesses |
|----------|-----------|-----------|
| **Direct HTML generation** | Simple, single-shot | Long contexts, poor layout, layout-unaware |
| **Template-based** | Constrained, safe | Generic, monotonous, low interactivity |
| **Agent-based (proposed)** | Iterative refinement, layout awareness, multimedia integration | Requires MLLM with visual perception, multi-round |

## Broader Applicability

This pattern generalizes beyond papers:
- Design → HTML (Sketch2Code, Design2Code)
- Screenshots → HTML (Interaction2Code)
- Wireframes → Code (UICopilot, DCGen)

The core insight—*decompose, structure, refine—* applies to any source-to-web task where layout and aesthetics matter.

## Open Questions

1. **Convergence**: How many refinement iterations are necessary? Is there a principled stopping criterion?
2. **Scalability**: Does the approach work on pages with 100+ sections or complex nested layouts?
3. **Hallucination**: Can joint global-local reasoning eliminate layout-breaking edits?
4. **Generalization**: How well do agents trained on papers transfer to other document types?

---

**Related Pages:** [[paper-to-web-conversion]], [[mcp-model-context-protocol]]

**Sources:** ^[raw/papers/paper2web.md]
