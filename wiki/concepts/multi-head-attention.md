---
title: Multi-Head Attention
created: 2025-01-27
updated: 2025-01-27
type: concept
tags: [architecture]
sources: [raw/papers/attention_is_all_you_need.md]
authors: [Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, Łukasz Kaiser, Illia Polosukhin]
confidence: high
---

# Multi-Head Attention

Multi-Head Attention (MHA) runs **h parallel attention operations** on linearly projected versions of queries, keys, and values, then concatenates and re-projects the results. It is a core component of the [[transformer-architecture]], proposed in "Attention Is All You Need" (Vaswani et al., 2017).

## Motivation

A single [[scaled-dot-product-attention]] head averages across all value positions — averaging inhibits the ability to attend to information from **different representation subspaces** at different positions simultaneously. Multi-head attention addresses this by learning h different projection functions, each specializing in a different aspect.

## Formula

```
MultiHead(Q, K, V) = Concat(head₁, ..., headₕ) · Wᴼ
where  headᵢ = Attention(Q·Wᵢᴼ, K·Wᵢᴷ, V·Wᵢᵛ)
```

**Projection matrices:**
- Wᵢᴼ ∈ ℝ^(d_model × d_k)
- Wᵢᴷ ∈ ℝ^(d_model × d_k)  
- Wᵢᵛ ∈ ℝ^(d_model × d_v)
- Wᴼ ∈ ℝ^(h·d_v × d_model)

## Default Hyperparameters (Base Transformer)

| Parameter | Value |
|-----------|-------|
| h (heads) | 8 |
| d_k | 64 (= d_model / h = 512 / 8) |
| d_v | 64 |
| d_model | 512 |

By keeping d_k = d_model / h, the total computational cost of MHA is similar to single-head attention at full dimensionality.

## Ablation Results

From Table 3 of the paper (EN-DE dev BLEU):

| Heads | d_k | BLEU |
|-------|-----|------|
| 1 | 512 | 24.9 |
| 4 | 128 | 25.5 |
| **8** | **64** | **25.8** (base) |
| 16 | 32 | 25.8 |
| 32 | 16 | 25.4 |

Single-head is 0.9 BLEU worse than the best setting. Too many heads also degrades quality, suggesting there's a sweet spot.

Reducing d_k (rows B) also hurts quality — determining compatibility between queries and keys is non-trivial, and dot-product may not be the optimal compatibility function.

## Uses in the Transformer

1. **Encoder self-attention** — h=8 heads, all positions attend to all positions
2. **Decoder masked self-attention** — h=8 heads, with causal masking
3. **Encoder-decoder cross-attention** — decoder queries attend to encoder outputs

## Related Concepts

- [[self-attention]] — The underlying mechanism MHA extends
- [[scaled-dot-product-attention]] — The attention function used within each head
- [[transformer-architecture]] — The model that introduces and relies on MHA
