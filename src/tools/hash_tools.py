"""Hashing tools for safe wiki ingest bookkeeping."""

from __future__ import annotations

import hashlib

from langchain_core.tools import tool


@tool()
def compute_sha256(text: str, lstrip_newlines: bool = True) -> str:
    """Compute a SHA-256 hex digest for text using the wiki raw-source convention.

    Args:
        text: Text to hash.
        lstrip_newlines: When True, strip leading newlines before hashing. This
            matches the wiki raw-source frontmatter convention: hash the body
            after the closing `---` delimiter using ``body.lstrip("\\n")``.

    Returns:
        64-character SHA-256 hex digest.
    """
    body = text.lstrip("\n") if lstrip_newlines else text
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
