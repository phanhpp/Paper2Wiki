---
title: Scaled Dot-Product Attention
created: 2025-01-27
updated: 2025-01-27
type: concept
tags: [architecture]
sources: [raw/papers/attention_is_all_you_need.md]
authors: [Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, Łukasz Kaiser, Illia Polosukhin]
confidence: high
---

# Scaled Dot-Product Attention

Scaled Dot-Product Attention is the specific attention function used in the [[transformer-architecture]], proposed in "Attention Is All You Need" (Vaswani et al., 2017). It is the building block of [[multi-head-attention]].

## Formula

```
Attention(Q, K, V) = softmax(QKᵀ / √d_k) · V
```

- **Q** (queries): matrix of query vectors, dimension d_k
- **K** (keys): matrix of key vectors, dimension d_k
- **V** (values): matrix of value vectors, dimension d_v
- **√d_k**: scaling factor

The output is a weighted sum of the values V, where the weight for each value is the softmax-normalized dot product of the query with the corresponding key.

## Why Scale by √d_k?

Without scaling, large values of d_k cause dot products to grow large in magnitude, pushing softmax into regions with **extremely small gradients** (saturation). This is because if query and key components are independent random variables with mean 0 and variance 1, their dot product has variance d_k.

Dividing by √d_k normalizes the variance back to 1, keeping gradients healthy during training.

## Comparison: Dot-Product vs Additive Attention

| Property | Scaled Dot-Product | Additive (Bahdanau) |
|----------|-------------------|---------------------|
| Compatibility fn | Dot product | Feed-forward network |
| Speed | ✅ Fast (matrix multiply) | ❌ Slower |
| Memory | ✅ Space-efficient | ❌ More parameters |
| Small d_k | Similar performance | Similar performance |
| Large d_k (unscaled) | ❌ Degrades (saturation) | ✅ Better |
| Large d_k (scaled) | ✅ Competitive | ✅ Better |

Dot-product attention is preferred in practice because highly optimized matrix multiplication code makes it significantly faster and more memory-efficient.

## Masking

For decoder [[self-attention]], illegal connections (attending to future positions) are masked by setting them to **-∞** before the softmax, which makes their weight → 0. This enforces the auto-regressive property.

## Computational Complexity

- Per layer: **O(n² · d)** where n = sequence length, d = dimension
- Sequential operations: **O(1)** — fully parallelizable
- Maximum dependency path length: **O(1)** — any two positions are directly connected

This is the key advantage over recurrent layers (O(n·d²) complexity, O(n) sequential ops, O(n) max path length).

## Related Concepts

- [[multi-head-attention]] — Runs h parallel instances of this attention function
- [[self-attention]] — The broader mechanism this implements
- [[transformer-architecture]] — The model context where this is used
