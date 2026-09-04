"""Resilient HTTP for connectors.

Ported from OpenWiki's ``src/connectors/http.ts``. Plain ``requests.get`` has no
timeout and no retry, so one 429 or a transient 5xx aborts a whole fetch, and an
unresponsive server hangs it forever.

What this adds:

- a per-attempt timeout, so no single request can hang the run
- bounded exponential backoff with **full jitter** on 429 and 5xx, honouring
  ``Retry-After`` **within the cap** — a server saying "retry in one hour" must
  not stall us for an hour
- the same backoff on network errors (connection reset, DNS, timeout)

**401 and 403 are returned unretried, deliberately.** They are not transient, and
a caller needs to *see* a 401 to trigger a token refresh (see the auth work in
items 14-15). Retrying them wastes attempts and can
lock accounts.
"""

from __future__ import annotations

import email.utils
import logging
import random as _random
import time as _time
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_BASE_DELAY_S = 0.5
MAX_BACKOFF_S = 20.0


def is_retryable_status(status: int) -> bool:
    """True for a rate limit or a server error — the transient cases."""
    return status == 429 or 500 <= status <= 599


def parse_retry_after_s(header_value: str | None, now: float | None = None) -> float | None:
    """Parse ``Retry-After`` into seconds. Handles both forms the spec allows.

    Delta-seconds (``120``) or an HTTP-date (``Wed, 21 Oct 2015 07:28:00 GMT``).
    Returns None when absent or unparseable. ``now`` is injectable so the
    HTTP-date branch is testable without depending on the clock.
    """
    if not header_value:
        return None
    trimmed = header_value.strip()
    if trimmed.isdigit():
        return float(trimmed)
    try:
        # Raises on malformed input in 3.10+ (it does not return None), and a
        # server sending junk here must not take the fetch down with it.
        parsed = email.utils.parsedate_to_datetime(trimmed)
    except (TypeError, ValueError):
        return None
    now = _time.time() if now is None else now
    return max(0.0, parsed.timestamp() - now)


def _backoff_delay_s(attempt: int, base_delay_s: float, rand: Callable[[], float]) -> float:
    """Exponential backoff with full jitter, capped.

    Full jitter (``random() * ceiling``, not ``ceiling/2 + random()``) so that
    concurrent clients de-correlate their retries instead of retrying in step.
    """
    ceiling = min(MAX_BACKOFF_S, base_delay_s * (2**attempt))
    return rand() * ceiling


def fetch_with_resilience(
    url: str,
    *,
    method: str = "GET",
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_retries: int = DEFAULT_MAX_RETRIES,
    base_delay_s: float = DEFAULT_BASE_DELAY_S,
    sleep: Callable[[float], None] | None = None,
    rand: Callable[[], float] | None = None,
    **kwargs: Any,
) -> requests.Response:
    """``requests`` with a per-attempt timeout and bounded retry/backoff.

    Non-transient responses (2xx/3xx/4xx other than 429) are returned to the
    caller unchanged after the first attempt.

    ``sleep`` and ``rand`` are injectable purely for tests: backoff then costs
    nothing and jitter can be pinned to an exact delay. Without this the retry
    paths are untestable in a unit suite.
    """
    do_sleep = sleep or _time.sleep
    do_rand = rand or _random.random
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            response = requests.request(method, url, timeout=timeout_s, **kwargs)

            if attempt < max_retries and is_retryable_status(response.status_code):
                retry_after = parse_retry_after_s(response.headers.get("Retry-After"))
                delay = min(
                    MAX_BACKOFF_S,
                    retry_after if retry_after is not None
                    else _backoff_delay_s(attempt, base_delay_s, do_rand),
                )
                # Release the connection back to the pool before sleeping.
                response.close()
                logger.debug("retrying %s after %.1fs (status %s)", url, delay, response.status_code)
                do_sleep(delay)
                continue

            return response

        except requests.RequestException as exc:
            last_error = exc
            if attempt >= max_retries:
                break
            do_sleep(_backoff_delay_s(attempt, base_delay_s, do_rand))

    raise last_error if last_error else RuntimeError(f"fetch failed: {url}")
