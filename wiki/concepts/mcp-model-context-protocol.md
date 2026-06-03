---
title: Model Context Protocol (MCP)
created: 2026-06-01
updated: 2026-06-01
type: concept
tags: [inference, architecture, alignment]
sources: [raw/papers/paper2web.md]
confidence: medium
---

# Model Context Protocol (MCP)

**Definition:** A standardized protocol and architecture pattern for encapsulating document or domain assets (text, images, links) as queryable resources with stable identifiers, relational metadata, and tool-based access—enabling structured, multi-agent reasoning over unstructured source material.

## Problem MCP Solves

When converting papers to webpages (or similar structured synthesis tasks):
- Source documents are large and unstructured (PDFs, Markdown)
- LLM context windows are limited; naive insertion causes information loss
- Direct end-to-end generation struggles with layout and interactivity
- Multiple agents may need coordinated access to the same assets
- Iterative refinement requires precise reference to specific components

**Solution:** MCP server acts as a managed resource repository, exposing assets via well-defined tools rather than raw text.

## Architecture

### Components

1. **Resource Repository**
   - Stores decomposed assets (textual sections, figures, links, metadata)
   - Each resource has stable unique ID (rid)
   - Enriched with cross-references and relational metadata

2. **Tool Suite** (registered with MCP server)
   - `enumerate_resources()` — List all assets of a given type
   - `retrieve_resource(rid)` — Fetch asset content, caption, context
   - `update_asset(rid, edits)` — Modify specific asset (e.g., reposition a figure)
   - `compute_layout_budget(rid)` — Get spatial footprint estimate

3. **Multi-Agent Interface**
   - Agents invoke tools to read and modify resources
   - Agents receive grounded responses (not hallucinated)
   - No direct text insertion; all edits via tool calls

### Asset Types

**Textual Resources:**
- Stable ID (e.g., `text_intro_001`)
- Full paragraph text
- LLM-generated synopsis
- Citation count, section metadata

**Visual Resources:**
- Image ID and path
- Caption and label
- Backlinks to citing paragraphs
- Estimated footprint (width, height)

**Link Resources:**
- URL and ID
- Semantic role (citation, demo link, code repo, etc.)
- Anchor text
- Validity and accessibility metadata

## Benefits

### For Agents
- **Stability**: Asset IDs don't change across iterations; tool responses are deterministic
- **Grounding**: References in HTML always resolve correctly
- **Efficiency**: Agents don't resend full document; just query/edit by ID

### For Refinement
- **Targeted edits**: Agent can modify a single figure's position without regenerating the full page
- **Accountability**: Each edit is traceable to a specific asset and tool call
- **Convergence**: Prevents hallucinations (agents can't invent assets)

### For Orchestration
- **Multi-agent coordination**: Multiple agents can safely access the same repository
- **Caching**: Results of expensive operations (image preprocessing, layout heuristics) are cached
- **Scalability**: Works for papers with 50+ sections, 100+ figures

## Real-World Example: PWAgent

In **Paper2Web's PWAgent**, the MCP server:
1. Receives decomposed paper assets (from Stage 1)
2. Enriches them with cross-modal links (Stage 2)
3. Exposes tools for webpage synthesis and editing (Stage 3)
4. Supports the Orchestrator Agent's iterative refinement loop
   - Agent calls `list_textual_assets()` → `retrieve_resource('section_results')` → `update_asset('fig_5', {x: 150, y: 400})`

## Broader Context

MCP is being standardized as a general-purpose protocol:
- **Anthropic & ecosystem**: Defining interoperable server/client patterns
- **Use cases**: Document analysis, code repositories, database queries, external APIs
- **Adoption**: Emerging in agentic systems (Paper2Agent, etc.)

## Limitations & Open Questions

1. **Schema Definition**: How to standardize resource schemas across domains (papers, designs, code)?
2. **Consistency**: Ensuring edits via tool calls don't break downstream dependencies
3. **Composability**: Can MCP servers be chained (MCP → MCP → output)?
4. **Version Control**: How to track resource changes over refinement iterations?

---

**Related Pages:** [[agent-based-webpage-generation]], [[paper-to-web-conversion]]

**Sources:** ^[raw/papers/paper2web.md]
