---
title: Self-Attention
created: 2025-01-27
updated: 2025-01-27
type: concept
tags: [architecture, training]
sources: [raw/papers/attention_is_all_you_need.pdf]
authors: [Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, Łukasz Kaiser, Illia Polosukhin]
confidence: high
---

# Self-Attention

Self-attention (also called *intra-attention*) is an attention mechanism that relates different positions **within the same sequence** to compute a representation of that sequence. It is the fundamental building block of the [[transformer-architecture]].

## Core Idea

An attention function maps a **query** and a set of **key-value pairs** to an output. The output is a weighted sum of the values, where the weight for each value is determined by the compatibility of the query with the corresponding key.

In self-attention, the queries, keys, and values all come from the same source (the previous layer's output), allowing every position to attend to every other position in the sequence.

## Advantages over Recurrence

| Property | Self-Attention | Recurrent (RNN) |
|----------|---------------|-----------------|
| Sequential operations | O(1) | O(n) |
| Max path length (long-range deps) | O(1) | O(n) |
| Complexity per layer | O(n²·d) | O(n·d²) |
| Parallelizable | ✅ Fully | ❌ Inherently sequential |
| Interpretability | ✅ Attention maps are inspectable | ❌ Hidden states opaque |

Self-attention is faster than recurrence when sequence length n < representation dimension d — the common case for modern NLP models.

## Types of Self-Attention in the Transformer

The [[transformer-architecture]] uses self-attention in three distinct ways:

1. **Encoder self-attention:** All positions attend to all positions in the previous encoder layer
2. **Decoder masked self-attention:** Each position attends only to positions ≤ itself (auto-regressive masking; sets future positions to -∞ before softmax)
3. **Encoder-decoder cross-attention:** Decoder queries attend to all encoder key-value pairs

## Implementation

Self-attention is implemented as [[scaled-dot-product-attention]] and typically extended to [[multi-head-attention]] for richer representations.

## Interpretability

Individual attention heads learn to perform different tasks. Many exhibit behavior related to syntactic and semantic structure — e.g., one head may track subject-verb agreement, another coreference. Attention distributions are visualizable and inspectable, unlike RNN hidden states.

## Historical Context

Self-attention was used in NLP tasks (reading comprehension, summarization, textual entailment) before the Transformer, but always in conjunction with recurrent networks. The Transformer (2017) was the first model to use self-attention *exclusively*, removing the recurrent component entirely.

## Related Concepts

- [[transformer-architecture]] — The model built entirely on self-attention
- [[scaled-dot-product-attention]] — The specific formula used for the attention function
- [[multi-head-attention]] — Running multiple self-attention operations in parallel
- [[positional-encoding]] — Required because self-attention is position-invariant by default
