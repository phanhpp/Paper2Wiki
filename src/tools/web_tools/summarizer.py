"""
summarizer.py — Tiered LLM compression for extracted web content.

Same architecture as Hermes process_content_with_llm():
  < 5k chars   → skip (return None, caller keeps raw content)
  5k–500k      → single LLM call → structured markdown summary
  500k–2M      → chunked: split → parallel LLM per chunk → synthesis
  > 2M         → refuse

Uses Anthropic SDK with Haiku as the auxiliary model.
Hermes uses OpenRouter + Gemini Flash — same role, different provider.

Hermes equivalents:
- process_content_with_llm()            → summarize()
- _call_summarizer_llm()                → _call_llm()
- _process_large_content_chunked()      → _chunked_summarize()

Config keys (from registry.load_config()):
- summarizer_model: model to use (default: claude-haiku-4-5-20251001)
- min_length_for_summary: threshold in chars (default: 5000)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import anthropic

logger = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────

DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_MIN_LENGTH = 5000
MAX_OUTPUT = 5000          # hard cap on final summary size
CHUNK_THRESHOLD = 500_000  # above this, use chunked processing
CHUNK_SIZE = 100_000       # chars per chunk
MAX_CONTENT = 2_000_000    # refuse above this

# ── Prompts ───────────────────────────────────────────────────────────────────
# Hermes uses separate system prompts for full-doc vs chunk summarization.
# We do the same.

_SYSTEM_FULL = (
    "You are an expert content analyst. Process web content into a "
    "comprehensive yet concise markdown summary that preserves all important "
    "information while dramatically reducing bulk.\n\n"
    "Include:\n"
    "1. Key excerpts (quotes, code snippets, important facts) in original format\n"
    "2. Comprehensive summary of all other important information\n"
    "3. Proper markdown formatting with headers, bullets, and emphasis\n\n"
    "Never lose key facts, figures, insights, or actionable information."
)

_SYSTEM_CHUNK = (
    "You are an expert content analyst processing a SECTION of a larger document. "
    "Extract and summarize key information from THIS SECTION ONLY.\n\n"
    "Guidelines:\n"
    "1. Do NOT write introductions or conclusions — this is partial content\n"
    "2. Extract ALL key facts, figures, data points, and insights\n"
    "3. Preserve important quotes, code snippets, and specific details verbatim\n"
    "4. Use bullet points and structured formatting for easy synthesis later"
)

_SYSTEM_SYNTHESIS = (
    "You synthesize multiple summaries into one cohesive, comprehensive summary. "
    "Be thorough but concise. Remove redundancy, preserve all key facts."
)


# ── Client ────────────────────────────────────────────────────────────────────

_client: anthropic.AsyncAnthropic | None = None


def _get_client() -> anthropic.AsyncAnthropic:
    """Lazy-init async Anthropic client. Reads ANTHROPIC_API_KEY from env."""
    global _client
    if _client is None:
        _client = anthropic.AsyncAnthropic()
    return _client


def _resolve_config() -> dict[str, Any]:
    """Pull summarizer settings from config, with defaults."""
    try:
        from src.tools.web_tools.registry import load_config

        cfg = load_config()
    except Exception:
        cfg = {}

    return {
        "model": cfg.get("summarizer_model", DEFAULT_MODEL),
        "min_length": int(cfg.get("min_length_for_summary", DEFAULT_MIN_LENGTH)),
    }


# ── Core LLM call ─────────────────────────────────────────────────────────────

async def _call_llm(
    content: str,
    system: str,
    model: str | None = None,
    max_tokens: int = 4096,
) -> str | None:
    """Single LLM call with retry. Returns summary text or None.

    Hermes equivalent: _call_summarizer_llm()
    Two retries with exponential backoff — same as Hermes.
    """
    cfg = _resolve_config()
    effective_model = model or cfg["model"]
    client = _get_client()

    max_retries = 2
    delay = 2

    for attempt in range(max_retries):
        try:
            resp = await client.messages.create(
                model=effective_model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                temperature=0.1,
            )
            text = resp.content[0].text if resp.content else None
            if text:
                return text

            logger.warning("LLM returned empty (attempt %d/%d)", attempt + 1, max_retries)
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 2
        except Exception as e:
            logger.warning("LLM call failed (attempt %d/%d): %s", attempt + 1, max_retries, str(e)[:120])
            if attempt < max_retries - 1:
                await asyncio.sleep(delay)
                delay *= 2
            else:
                raise

    return None


# ── Chunked processing ────────────────────────────────────────────────────────

async def _chunked_summarize(
    content: str,
    context_str: str,
    model: str | None = None,
) -> str | None:
    """Split large content into chunks, summarize in parallel, synthesize.

    Hermes equivalent: _process_large_content_chunked()
    Same flow: chunk → parallel _call_llm → synthesis _call_llm
    """
    # Split into chunks
    chunks = [content[i : i + CHUNK_SIZE] for i in range(0, len(content), CHUNK_SIZE)]
    logger.info("Chunked: %d chunks of ~%d chars", len(chunks), CHUNK_SIZE)

    # Summarize each chunk in parallel
    async def summarize_chunk(idx: int, chunk: str) -> tuple[int, str | None]:
        try:
            prompt = (
                f"{context_str}"
                f"[Chunk {idx + 1} of {len(chunks)}]\n\n"
                f"SECTION CONTENT:\n{chunk}"
            )
            result = await _call_llm(prompt, _SYSTEM_CHUNK, model, max_tokens=2048)
            if result:
                logger.info("Chunk %d/%d: %d → %d chars", idx + 1, len(chunks), len(chunk), len(result))
            return idx, result
        except Exception as e:
            logger.warning("Chunk %d/%d failed: %s", idx + 1, len(chunks), str(e)[:50])
            return idx, None

    raw_results = await asyncio.gather(
        *[summarize_chunk(i, c) for i, c in enumerate(chunks)],
        return_exceptions=True,
    )

    # Collect successful summaries in order
    summaries = []
    for item in raw_results:
        if isinstance(item, BaseException):
            logger.warning("Chunk task failed: %s", item)
            continue
        idx, text = item
        if text:
            summaries.append((idx, text))

    if not summaries:
        return "[Failed to process: all chunk summarizations failed]"

    summaries.sort(key=lambda x: x[0])
    logger.info("Got %d/%d chunk summaries", len(summaries), len(chunks))

    # Single chunk — just return it
    if len(summaries) == 1:
        result = summaries[0][1]
        if len(result) > MAX_OUTPUT:
            result = result[:MAX_OUTPUT] + "\n\n[...truncated...]"
        return result

    # Synthesize into one summary
    combined = "\n\n---\n\n".join(
        f"## Section {idx + 1}\n{text}" for idx, text in summaries
    )
    synthesis_prompt = (
        f"Synthesize these section summaries into ONE cohesive summary "
        f"under {MAX_OUTPUT} characters.\n\n"
        f"{context_str}"
        f"SECTION SUMMARIES:\n{combined}"
    )

    try:
        final = await _call_llm(synthesis_prompt, _SYSTEM_SYNTHESIS, model)
        if not final:
            # Fallback: concatenate chunk summaries
            logger.warning("Synthesis returned empty — concatenating chunks")
            final = "\n\n".join(text for _, text in summaries)
    except Exception as e:
        logger.warning("Synthesis failed: %s — concatenating chunks", str(e)[:100])
        final = "\n\n".join(text for _, text in summaries)

    if len(final) > MAX_OUTPUT:
        final = final[:MAX_OUTPUT] + "\n\n[...truncated...]"

    return final


# ── Public API ────────────────────────────────────────────────────────────────

async def summarize(
    content: str,
    url: str = "",
    title: str = "",
    model: str | None = None,
    min_length: int | None = None,
) -> str | None:
    """Summarize web content using tiered LLM compression.

    Returns:
        Summarized markdown, or None if content is short enough to use raw.

    Hermes equivalent: process_content_with_llm()
    """
    cfg = _resolve_config()
    effective_min = min_length if min_length is not None else cfg["min_length"]

    content_len = len(content)

    # Tier 0: refuse absurdly large content
    if content_len > MAX_CONTENT:
        size_mb = content_len / 1_000_000
        logger.warning("Content too large: %.1fMB > 2MB limit", size_mb)
        return f"[Content too large to process: {size_mb:.1f}MB]"

    # Tier 1: skip if short enough
    if content_len < effective_min:
        logger.debug("Content short enough (%d < %d), skipping LLM", content_len, effective_min)
        return None

    # Build context header
    context_parts = []
    if title:
        context_parts.append(f"Title: {title}")
    if url:
        context_parts.append(f"Source: {url}")
    context_str = "\n".join(context_parts) + "\n\n" if context_parts else ""

    # Tier 2: chunked processing for large content
    if content_len > CHUNK_THRESHOLD:
        logger.info("Large content (%d chars) — chunked processing", content_len)
        return await _chunked_summarize(content, context_str, model)

    # Tier 3: single-pass summarization
    logger.info("Summarizing %d chars", content_len)
    prompt = f"{context_str}CONTENT TO PROCESS:\n{content}"

    try:
        result = await _call_llm(prompt, _SYSTEM_FULL, model)
    except Exception as e:
        # Fallback: truncated raw content (same as Hermes)
        logger.warning("Summarization failed: %s — returning truncated raw", str(e)[:120])
        truncated = content[:MAX_OUTPUT]
        if content_len > MAX_OUTPUT:
            truncated += f"\n\n[Truncated — {MAX_OUTPUT:,} of {content_len:,} chars. LLM summarization failed.]"
        return truncated

    if result and len(result) > MAX_OUTPUT:
        result = result[:MAX_OUTPUT] + "\n\n[...truncated...]"

    # If LLM returned nothing, fall back to truncated raw
    if not result:
        truncated = content[:MAX_OUTPUT]
        if content_len > MAX_OUTPUT:
            truncated += f"\n\n[Truncated — {MAX_OUTPUT:,} of {content_len:,} chars]"
        return truncated

    compression = len(result) / content_len
    logger.info("Summarized: %d → %d chars (%.1f%%)", content_len, len(result), compression * 100)
    return result
