# src/sessions/title_manager.py
"""
Title management for Paper2Wiki sessions.

Handles auto-generation, sanitization, uniqueness enforcement, and
lineage numbering for session titles. Mirrors Hermes's title system.

Public API:
    sanitize_title(title)                          -- clean + validate a title string
    get_next_title_in_lineage(conn, base_title)    -- "my project" → "my project #2"
    set_session_title(conn, session_id, title)     -- set title with uniqueness enforcement
    maybe_auto_title(conn, session_id, messages)    -- fire-and-forget auto-titling
"""

import re
import threading
import logging
from typing import Optional
from src.agents.llms import set_up_llms

logger = logging.getLogger(__name__)

MAX_TITLE_LENGTH = 100

_TITLE_PROMPT = (
    "Generate a short, descriptive title (3-7 words) for a conversation that starts with "
    "the following exchange. The title should capture the main topic or intent. "
    "Return ONLY the title text, nothing else. No quotes, no punctuation at the end, no prefixes."
)


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------

def sanitize_title(title: Optional[str]) -> Optional[str]:
    """Validate and sanitize a session title.

    - Strips leading/trailing whitespace
    - Removes ASCII control characters (0x00-0x1F, 0x7F)
    - Removes problematic Unicode: zero-width chars, RTL/LTR overrides,
      object replacement, interlinear annotation markers
    - Collapses internal whitespace runs to single spaces
    - Normalizes empty/whitespace-only strings to None
    - Enforces MAX_TITLE_LENGTH

    Args:
        title: Raw title string, or None.

    Returns:
        Cleaned title string, or None if empty after cleaning.

    Raises:
        ValueError: If the cleaned title exceeds MAX_TITLE_LENGTH.
    """
    if not title:
        return None

    # Remove ASCII control chars but keep \t \n \r for whitespace normalization
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', title)

    # Remove problematic Unicode control characters:
    # zero-width (U+200B-U+200F), directional overrides (U+202A-U+202E,
    # U+2066-U+2069), BOM (U+FEFF), object replacement (U+FFFC),
    # interlinear annotation (U+FFF9-U+FFFB)
    cleaned = re.sub(
        r'[\u200b-\u200f\u2028-\u202e\u2060-\u2069\ufeff\ufffc\ufff9-\ufffb]',
        '', cleaned,
    )

    # Collapse whitespace runs and strip
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()

    if not cleaned:
        return None

    if len(cleaned) > MAX_TITLE_LENGTH:
        raise ValueError(
            f"Title too long ({len(cleaned)} chars, max {MAX_TITLE_LENGTH})"
        )

    return cleaned


# ---------------------------------------------------------------------------
# Lineage
# ---------------------------------------------------------------------------

def get_next_title_in_lineage(conn, base_title: str) -> str:
    """Generate the next title in a compression/continuation lineage.

    Strips any existing ' #N' suffix to find the true base name, then
    finds the highest existing number across all variants and increments.

    Examples:
        "my project"   → "my project #2"  (if "my project" exists)
        "my project #2" → "my project #4" (if #2 and #3 already exist)

    Args:
        conn:       sqlite3.Connection to sessions.db.
        base_title: Title to extend (may already have a ' #N' suffix).

    Returns:
        Next available title string in the lineage.
    """
    # Strip existing #N suffix to find the true base
    match = re.match(r'^(.*?) #(\d+)$', base_title)
    base = match.group(1) if match else base_title

    # Escape SQL LIKE wildcards to prevent false matches
    escaped = base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

    rows = conn.execute(
        "SELECT title FROM sessions WHERE title = ? OR title LIKE ? ESCAPE '\\'",
        (base, f"{escaped} #%")
    ).fetchall()
    existing = [row[0] for row in rows]

    if not existing:
        return base  # no conflict, use base as-is

    # Find highest existing number; unnumbered original counts as #1
    max_num = 1
    for t in existing:
        m = re.match(r'^.* #(\d+)$', t)
        if m:
            max_num = max(max_num, int(m.group(1)))

    return f"{base} #{max_num + 1}"


# ---------------------------------------------------------------------------
# Setting titles
# ---------------------------------------------------------------------------

def set_session_title(conn, session_id: str, title: str) -> bool:
    """Set or update a session's title with sanitization and uniqueness enforcement.

    If the sanitized title already belongs to another session, automatically
    generates the next title in the lineage (e.g. "my project #2") rather
    than raising an error. This matches Hermes's auto-titling behavior where
    collisions are silently resolved via lineage numbering.

    For manual /title commands where the user explicitly chose a name,
    raise ValueError on collision instead (call sanitize_title + check
    uniqueness manually before calling this function).

    Args:
        conn:       sqlite3.Connection to sessions.db.
        session_id: ID of the session to title.
        title:      Raw title string (will be sanitized).

    Returns:
        True if the title was set, False if title was empty after sanitization.

    Raises:
        ValueError: If title exceeds MAX_TITLE_LENGTH after sanitization.
    """
    title = sanitize_title(title)
    if not title:
        return False

    # Explicit uniqueness check — don't rely on IntegrityError
    conflict = conn.execute(
        "SELECT id FROM sessions WHERE title = ? AND id != ?",
        (title, session_id)
    ).fetchone()

    if conflict:
        # Auto-titling: silently resolve via lineage numbering
        title = get_next_title_in_lineage(conn, title)

    conn.execute(
        "UPDATE sessions SET title = ? WHERE id = ?",
        (title, session_id)
    )
    conn.commit()
    return True


# ---------------------------------------------------------------------------
# Auto-title generation
# ---------------------------------------------------------------------------

def _generate_title(
    user_msg: str, 
    assistant_msg: str, 
 ) -> Optional[str]:
    """Call Haiku to generate a 3-7 word title from the first exchange.

    Truncates both messages to 500 chars to keep the request small and cheap.
    Cleans up common LLM output artifacts (wrapping quotes, "Title:" prefix).

    Args:
        user_msg:      First user message content.
        assistant_msg: First assistant response content.
    Returns:
        Generated title string, or None if generation fails or returns empty.
    """
    user_snippet = (user_msg or "")[:500]
    assistant_snippet = (assistant_msg or "")[:500]

    llm = set_up_llms("claude-haiku-4-5-20251001")

    response = llm.invoke([
        {"role": "system", "content": _TITLE_PROMPT},
        {"role": "user", "content": f"User: {user_snippet}\n\nAssistant: {assistant_snippet}"}
    ])

    title = (response.content or "").strip().strip('"\'')
    print("Session title: ", title)
    # Strip common LLM output prefixes
    if title.lower().startswith("title:"):
        title = title[6:].strip()

    # Hard cap before sanitization
    if len(title) > MAX_TITLE_LENGTH:
        title = title[:MAX_TITLE_LENGTH - 3] + "..."

    return title if title else None


def maybe_auto_title(
    conn, 
    session_id: str, 
    messages: list, 
) -> None:
    """Fire-and-forget: generate and set a session title in a background thread.

    Only runs if:
    - Both a user message and an assistant message exist in the session
    - The session does not already have a title (preserves manual /title)

    Collisions are resolved via lineage numbering in set_session_title().
    Failures are logged as warnings and never surface to the user.

    Args:
        conn:       sqlite3.Connection to sessions.db.
        session_id: ID of the session to title.
        messages:   Final message list from agent state (LangChain message objects).
    """
    # Extract first human and first ai message
    user_msg = next((m.content for m in messages if m.type == "human"), None)
    assistant_msg = next((m.content for m in messages if m.type == "ai"), None)

    if not user_msg or not assistant_msg:
        return  # nothing to title

    def _run():
        try:
            # Don't overwrite a manually set title
            row = conn.execute(
                "SELECT title FROM sessions WHERE id = ?",
                (session_id,)
            ).fetchone()
            if row and row[0]:
                return

            title = _generate_title(user_msg, assistant_msg)
            if title:
                set_session_title(conn, session_id, title)
                logger.debug("Auto-titled session %s: %s", session_id, title)
        except Exception as e:
            logger.warning("Title generation failed for session %s: %s", session_id, e)

    thread = threading.Thread(target=_run, daemon=True, name="auto-title")
    thread.start()