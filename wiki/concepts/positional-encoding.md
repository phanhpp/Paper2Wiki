---
title: Positional Encoding
created: 2025-01-27
updated: 2025-01-27
type: concept
tags: [architecture]
sources: [raw/papers/attention_is_all_you_need.md]
authors: [Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, Łukasz Kaiser, Illia Polosukhin]
confidence: high
---

# Positional Encoding

Positional Encoding is the mechanism used by the [[transformer-architecture]] to inject information about the order of tokens in a sequence. Since [[self-attention]] is **permutation-invariant** (it treats the input as a set, not a sequence), position information must be added explicitly.

## The Problem

Self-attention computes the same output regardless of the order of input tokens — shuffling the input produces a shuffled (but otherwise identical) output. Without positional information, the model cannot distinguish "dog bites man" from "man bites dog."

## Sinusoidal Positional Encoding

The paper uses fixed sinusoidal functions:

```
PE(pos, 2i)   = sin(pos / 10000^(2i / d_model))
PE(pos, 2i+1) = cos(pos / 10000^(2i / d_model))
```

- **pos**: position of the token in the sequence
- **i**: dimension index (0 to d_model/2 - 1)
- Each dimension corresponds to a sinusoid with a different frequency
- Wavelengths form a geometric progression from 2π to 10000·2π

The positional encodings are **added** (not concatenated) to the input embeddings. Both have dimension d_model = 512, so they are compatible for summation.

## Why Sinusoidal?

Two key properties motivated this choice:

1. **Relative position awareness:** For any fixed offset k, PE(pos+k) can be represented as a linear function of PE(pos). This allows the model to easily attend by relative positions.

2. **Extrapolation:** Sinusoidal encodings may generalize to sequence lengths longer than those seen during training — an advantage over learned embeddings which have no such guarantee.

## Sinusoidal vs Learned Positional Embeddings

From Table 3 (E) of the paper — nearly identical results:

| Method | PPL (dev) | BLEU (dev) |
|--------|-----------|------------|
| Sinusoidal (base) | 4.92 | 25.8 |
| Learned embeddings | 4.92 | 25.7 |

The sinusoidal variant was chosen for its potential extrapolation benefit, despite the negligible empirical difference.

## Where It's Applied

Positional encodings are added at the **bottom** of both the encoder and decoder stacks — once to the input embeddings, before any attention layers process them.

## Open Questions

- Sinusoidal encodings became less popular in later models (e.g., BERT, GPT) which use learned embeddings
- Relative positional encodings (e.g., ALiBi, RoPE) emerged as superior alternatives for long-context generalization — a limitation of the original absolute positional encoding

## Related Concepts

- [[self-attention]] — The position-invariant mechanism that necessitates positional encoding
- [[transformer-architecture]] — The model that first used this encoding scheme
- [[multi-head-attention]] — Processes position-encoded embeddings
