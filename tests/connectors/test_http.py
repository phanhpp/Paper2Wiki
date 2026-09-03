"""Retry/backoff behaviour, with no network and no real sleeping.

`sleep` and `rand` are injectable precisely so this file can exist: delays are
recorded instead of waited, and jitter is pinned to an exact value.
"""

from __future__ import annotations

import email.utils
import time

import pytest
import requests

from src.connectors.http import (
    MAX_BACKOFF_S,
    fetch_with_resilience,
    is_retryable_status,
    parse_retry_after_s,
)


class FakeResponse:
    def __init__(self, status_code: int, headers: dict | None = None):
        self.status_code = status_code
        self.headers = headers or {}
        self.closed = False

    def close(self):
        self.closed = True


def _stub(monkeypatch, responses):
    """Serve `responses` in order; record the calls."""
    calls = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url))
        result = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(requests, "request", fake_request)
    return calls


@pytest.mark.unit
@pytest.mark.parametrize("status,expected", [
    (429, True), (500, True), (503, True),
    (200, False), (401, False), (403, False), (404, False),
])
def test_which_statuses_are_retryable(status, expected):
    assert is_retryable_status(status) is expected


@pytest.mark.unit
def test_a_401_is_returned_immediately_not_retried(monkeypatch):
    """Load-bearing: a token-refresh layer must SEE the 401 to act on it."""
    calls = _stub(monkeypatch, [FakeResponse(401)])
    delays = []

    response = fetch_with_resilience("https://x", sleep=delays.append, rand=lambda: 1.0)

    assert response.status_code == 401
    assert len(calls) == 1          # no retry
    assert delays == []             # no sleeping


@pytest.mark.unit
def test_a_429_is_retried_then_returned(monkeypatch):
    calls = _stub(monkeypatch, [FakeResponse(429)])
    delays = []

    response = fetch_with_resilience(
        "https://x", max_retries=2, sleep=delays.append, rand=lambda: 1.0,
    )

    assert response.status_code == 429
    assert len(calls) == 3          # first attempt + 2 retries
    assert len(delays) == 2


@pytest.mark.unit
def test_backoff_grows_exponentially_and_is_capped(monkeypatch):
    _stub(monkeypatch, [FakeResponse(503)])
    delays = []

    fetch_with_resilience(
        "https://x", max_retries=8, base_delay_s=1.0,
        sleep=delays.append, rand=lambda: 1.0,     # full jitter pinned to its ceiling
    )

    assert delays[:4] == [1.0, 2.0, 4.0, 8.0]
    assert max(delays) <= MAX_BACKOFF_S


@pytest.mark.unit
def test_retry_after_is_honoured(monkeypatch):
    _stub(monkeypatch, [FakeResponse(429, {"Retry-After": "7"})])
    delays = []

    fetch_with_resilience("https://x", max_retries=1, sleep=delays.append, rand=lambda: 1.0)

    assert delays == [7.0]


@pytest.mark.unit
def test_retry_after_is_capped(monkeypatch):
    """A server saying "come back in an hour" must not stall the run for an hour."""
    _stub(monkeypatch, [FakeResponse(429, {"Retry-After": "3600"})])
    delays = []

    fetch_with_resilience("https://x", max_retries=1, sleep=delays.append, rand=lambda: 1.0)

    assert delays == [MAX_BACKOFF_S]


@pytest.mark.unit
def test_retry_after_accepts_an_http_date():
    future = email.utils.formatdate(time.time() + 100, usegmt=True)
    seconds = parse_retry_after_s(future)
    assert seconds is not None and 90 < seconds <= 100


@pytest.mark.unit
@pytest.mark.parametrize("value", [None, "", "not-a-date"])
def test_unparseable_retry_after_is_ignored(value):
    assert parse_retry_after_s(value) is None


@pytest.mark.unit
def test_the_body_is_released_before_retrying(monkeypatch):
    """Otherwise the connection can't be reused."""
    response = FakeResponse(500)
    _stub(monkeypatch, [response])

    fetch_with_resilience("https://x", max_retries=1, sleep=lambda _: None, rand=lambda: 0.0)

    assert response.closed


@pytest.mark.unit
def test_network_errors_retry_then_raise(monkeypatch):
    calls = _stub(monkeypatch, [requests.ConnectionError("dns")])
    delays = []

    with pytest.raises(requests.ConnectionError):
        fetch_with_resilience("https://x", max_retries=2, sleep=delays.append, rand=lambda: 0.5)

    assert len(calls) == 3
    assert len(delays) == 2


@pytest.mark.unit
def test_a_success_is_returned_without_sleeping(monkeypatch):
    _stub(monkeypatch, [FakeResponse(200)])
    delays = []

    response = fetch_with_resilience("https://x", sleep=delays.append, rand=lambda: 1.0)

    assert response.status_code == 200
    assert delays == []
