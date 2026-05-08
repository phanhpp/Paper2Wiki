"""Unit tests for ``GuardedLocalShellBackend`` sensitive-path blocking."""
from __future__ import annotations

import pytest

from src.agents.backend_wrapper import GuardedLocalShellBackend


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.local",
        "config/secrets.prod",
        "config/credentials.json",
        "~/.ssh/id_rsa",
        "keys/server.pem",
        "keys/server.key",
        ".aws/credentials",
    ],
)
def test_is_sensitive_blocks_expected_paths(path: str) -> None:
    """Paths matching env, keys, or cloud cred patterns are treated as sensitive."""
    backend = GuardedLocalShellBackend(root_dir=".", virtual_mode=True)
    assert backend._is_sensitive(path) is True


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "README.md",
        "skills/trace-analysis/SKILL.md",
        "src/tools/fetch_traces.py",
    ],
)
def test_is_sensitive_allows_regular_project_files(path: str) -> None:
    """Normal repo paths are not flagged so read/write can proceed (subject to backend rules)."""
    backend = GuardedLocalShellBackend(root_dir=".", virtual_mode=True)
    assert backend._is_sensitive(path) is False


@pytest.mark.unit
def test_read_blocks_sensitive_paths() -> None:
    """Verify sensitive files cannot be read through the guarded backend.

    This checks the actual ``read`` method, not just path classification, so a
    future refactor cannot accidentally bypass the secret-file guard.
    """
    backend = GuardedLocalShellBackend(root_dir=".", virtual_mode=True)

    result = backend.read(".env")

    assert result.error == "Reading .env is blocked"


@pytest.mark.unit
def test_write_blocks_sensitive_paths() -> None:
    """Verify sensitive files cannot be overwritten through the guarded backend.

    The guard should reject write attempts before the underlying shell backend
    has a chance to touch credentials or key material.
    """
    backend = GuardedLocalShellBackend(root_dir=".", virtual_mode=True)

    result = backend.write("config/credentials.json", "{}")

    assert result.error == "Writing config/credentials.json is blocked"


@pytest.mark.unit
def test_edit_blocks_sensitive_paths() -> None:
    """Verify sensitive files cannot be patched through the guarded backend.

    This covers the edit path separately from read/write because agent file
    updates often use patch-style edits.
    """
    backend = GuardedLocalShellBackend(root_dir=".", virtual_mode=True)

    result = backend.edit("keys/server.key", "old", "new")

    assert result.error == "Editing keys/server.key is blocked"
