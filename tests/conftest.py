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

# Colour off, at import time and for the same reason as above.
#
# CI sets FORCE_COLOR, and Rich then styles an option name in pieces —
# ``\x1b[1;36m-\x1b[0m\x1b[1;36m-model\x1b[0m`` — so the literal "--model" is not in the
# output and any assertion on rendered text fails there while passing locally. NO_COLOR
# also stops spinner frames leaking into captured output.
os.environ["NO_COLOR"] = "1"
os.environ["TERM"] = "dumb"
os.environ.pop("FORCE_COLOR", None)

# A fixed, wide terminal. Rich truncates table cells to fit ("https://openrouter.ai/…"),
# so any test reading a value out of a table depends on the width it happened to run at.
os.environ["COLUMNS"] = "200"
os.environ["LINES"] = "50"


@pytest.fixture(scope="session")
def langsmith_experiment_metadata() -> dict:
    return {
        "git_sha": os.environ.get("GITHUB_SHA", "local"),
        "branch": os.environ.get("GITHUB_REF_NAME", "local"),
        "model": "claude-haiku-4-5-20251001",
        "environment": os.environ.get("ENV", "local"),
    }
