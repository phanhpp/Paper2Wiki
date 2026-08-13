---
title: Transformer Architecture
created: 2025-01-27
updated: 2025-01-27
type: concept
tags: [architecture, model, training]
sources: [raw/papers/attention_is_all_you_need.md]
authors: [Ashish Vaswani, Noam Shazeer, Niki Parmar, Jakob Uszkoreit, Llion Jones, Aidan N. Gomez, Łukasz Kaiser, Illia Polosukhin]
confidence: high
---

# Transformer Architecture

The Transformer is a sequence transduction model introduced in "Attention Is All You Need" (Vaswani et al., 2017). It is the first transduction architecture that relies **entirely on attention mechanisms**, dispensing with recurrence and convolutions.

## Key Innovation

Prior state-of-the-art models (RNN, LSTM, GRU, ConvS2S) required sequential computation, making parallelization difficult and long-range dependency learning expensive. The Transformer replaces recurrent layers with [[self-attention]], achieving:

- **Full parallelization** during training — no sequential hidden state dependencies
- **Constant-length dependency paths** between any two positions (O(1) vs O(n) for RNNs)
- **Superior translation quality** at a fraction of the training cost

## Architecture Overview

The Transformer follows an **encoder-decoder** structure:

### Encoder
- Stack of **N = 6** identical layers
- Each layer has two sub-layers:
  1. [[multi-head-attention|Multi-Head Self-Attention]]
  2. Position-wise Feed-Forward Network (FFN): `FFN(x) = max(0, xW₁ + b₁)W₂ + b₂`
- Residual connection + LayerNorm around each sub-layer: `LayerNorm(x + Sublayer(x))`
- All sub-layers output dimension: **d_model = 512**

### Decoder
- Stack of **N = 6** identical layers
- Each layer has three sub-layers:
  1. Masked [[multi-head-attention|Multi-Head Self-Attention]] (prevents attending to future positions)
  2. [[multi-head-attention|Multi-Head Cross-Attention]] over encoder output
  3. Position-wise FFN
- Masking enforces auto-regressive property: position *i* can only attend to positions < *i*

### Embeddings & Output
- Learned embeddings for input/output tokens, scaled by √d_model
- Shared weight matrix between both embedding layers and pre-softmax linear transformation
- [[positional-encoding]] added to embeddings (no recurrence = no inherent position awareness)

## Hyperparameters (Base Model)

| Parameter | Value |
|-----------|-------|
| N (layers) | 6 |
| d_model | 512 |
| d_ff | 2048 |
| h (heads) | 8 |
| d_k = d_v | 64 |
| P_drop | 0.1 |
| ε_ls (label smoothing) | 0.1 |

**Big model:** d_model = 1024, d_ff = 4096, h = 16, P_drop = 0.3 — achieves 28.4 BLEU (EN-DE).

## Results

| Task | Score | Notes |
|------|-------|-------|
| WMT 2014 EN-DE | 28.4 BLEU | +2 BLEU over prior SOTA ensembles |
| WMT 2014 EN-FR | 41.8 BLEU | New single-model SOTA, trained in 3.5 days on 8 P100s |
| WSJ Constituency Parsing | 92.7 F1 | Semi-supervised; competitive without task-specific tuning |

## Training
- **Optimizer:** Adam (β₁=0.9, β₂=0.98, ε=10⁻⁹) with warmup learning rate schedule
- **Warmup:** Linear LR increase for 4,000 steps, then decay ∝ step_num^(-0.5)
- **Regularization:** Residual dropout (P_drop=0.1), label smoothing (ε_ls=0.1)
- **Base model:** 100K steps (~12 hours on 8 P100s)

## Why Self-Attention over RNN/CNN?

| Layer Type | Complexity/layer | Sequential Ops | Max Path Length |
|------------|-----------------|----------------|-----------------|
| Self-Attention | O(n²·d) | O(1) | O(1) |
| Recurrent | O(n·d²) | O(n) | O(n) |
| Convolutional | O(k·n·d²) | O(1) | O(log_k(n)) |

Self-attention connects all positions in O(1) steps, making long-range dependency learning far easier than RNNs.

## Related Concepts

- [[self-attention]] — Core mechanism enabling the architecture
- [[multi-head-attention]] — The parallel multi-head variant used throughout
- [[scaled-dot-product-attention]] — The specific attention formula
- [[positional-encoding]] — How position information is injected without recurrence
