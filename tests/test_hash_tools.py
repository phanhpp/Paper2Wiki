"""Unit tests for safe hashing tools."""

from __future__ import annotations

import hashlib

import pytest

from src.tools.hash_tools import compute_sha256


@pytest.mark.unit
def test_compute_sha256_defaults_to_lstrip_newlines() -> None:
    """Default behavior matches the wiki raw-source hash convention."""
    body = "\n\n# Title\n\nContent"
    expected = hashlib.sha256(body.lstrip("\n").encode("utf-8")).hexdigest()

    assert compute_sha256.invoke({"text": body}) == expected


@pytest.mark.unit
def test_compute_sha256_can_hash_exact_text() -> None:
    """lstrip_newlines=False preserves leading newlines in the digest input."""
    body = "\n\n# Title\n\nContent"
    expected = hashlib.sha256(body.encode("utf-8")).hexdigest()

    assert compute_sha256.invoke({"text": body, "lstrip_newlines": False}) == expected


@pytest.mark.unit
def test_compute_sha256_handles_empty_string() -> None:
    """Empty text hashes to the standard SHA-256 digest of empty bytes."""
    expected = hashlib.sha256(b"").hexdigest()

    assert compute_sha256.invoke({"text": ""}) == expected
