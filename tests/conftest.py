"""Shared pytest fixtures for all test suites.

Tracing is disabled here at *import* time, not in a fixture. pytest imports this
file before any test module, so this runs before something like
``tests/test_cli.py`` pulls in the agent package. A fixture would run too late:
by then the modules are imported, and ``langsmith.utils.get_env_var`` is
``lru_cache``d, so a later change to the environment may never be read.

Tests that genuinely want tracing (``-m langsmith``) set it back themselves.
"""

from __future__ import annotations

import os

import pytest

os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"  # the older name, still honoured


@pytest.fixture(scope="session")
def langsmith_experiment_metadata() -> dict:
    return {
        "git_sha": os.environ.get("GITHUB_SHA", "local"),
        "branch": os.environ.get("GITHUB_REF_NAME", "local"),
        "model": "claude-haiku-4-5-20251001",
        "environment": os.environ.get("ENV", "local"),
    }
