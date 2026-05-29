"""Shared pytest fixtures for all test suites."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session")
def langsmith_experiment_metadata() -> dict:
    return {
        "git_sha": os.environ.get("GITHUB_SHA", "local"),
        "branch": os.environ.get("GITHUB_REF_NAME", "local"),
        "model": "claude-haiku-4-5-20251001",
        "environment": os.environ.get("ENV", "local"),
    }
