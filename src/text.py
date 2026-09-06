"""Normalising LLM message content across providers.

Providers do not agree on the shape of a message's ``content``:

    Anthropic   "hello"
    Gemini      [{"type": "text", "text": "hello"}]

Any code that calls ``.strip()``, slices, or otherwise treats content as a string will
work on one provider and raise ``'list' object has no attribute 'strip'`` on another —
which is exactly how session auto-titling broke the first time it met Gemini.

Route every message body through :func:`as_text` before touching it.
"""

from __future__ import annotations


def as_text(content) -> str:
    """Flatten a message's content to plain text.

    Handles a plain string, a list of text/dict blocks, or anything else (stringified as
    a last resort, so this never raises).
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text") or block.get("content") or str(block))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return "" if content is None else str(content)
