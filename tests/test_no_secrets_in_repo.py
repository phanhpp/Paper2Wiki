"""Guardrail: fail if git-tracked text files contain strings that look like real secrets.

Uses ``git ls-files`` so only committed paths are scanned (not local ``.env``).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest


SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}"),
    re.compile(r"lsv2_[A-Za-z0-9_-]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(r"xapp-[0-9]-[A-Za-z0-9-]{20,}"),  # Slack app-level (Socket Mode) token
    re.compile(r"AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def _repo_root() -> Path:
    """Repository root (parent of ``tests/``)."""
    return Path(__file__).resolve().parents[1]


@pytest.mark.unit
def test_no_obvious_secrets_in_tracked_text_files() -> None:
    """Every tracked text file must pass regex checks; README placeholders are stripped first."""
    repo = _repo_root()
    offenders: list[str] = []
    skip_dirs = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}
    skip_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".lock"}

    tracked_files = subprocess.check_output(
        ["git", "ls-files"], cwd=repo, text=True
    ).splitlines()

    for rel in tracked_files:
        path = repo / rel
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() in skip_suffixes:
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue

        # Allow explicit placeholders in docs/examples.
        text = text.replace("sk-ant-...", "").replace("lsv2_...", "")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                offenders.append(str(path.relative_to(repo)))
                break

    assert offenders == [], f"Potential secrets found in files: {offenders}"


@pytest.mark.unit
def test_shell_env_passes_no_secrets(monkeypatch):
    """`execute`'s environment is an allowlist, so a new API key cannot leak into it.

    The shell backend defaults to an *empty* environment, which broke `gh` (no HOME → it
    reports "not logged into any GitHub hosts"). The fix passes a fixed list of variables
    through — never `inherit_env=True`, which would hand every provider key to any command
    the model decides to run.
    """
    from src.agents.backend_wrapper import SHELL_ENV_PASSTHROUGH, shell_env

    # Secrets that exist in a real .env, plus one invented to prove the list is closed.
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LANGSMITH_API_KEY",
                 "DAYTONA_API_KEY", "SLACK_BOT_TOKEN", "FIRECRAWL_API_KEY",
                 "SOME_FUTURE_API_KEY"):
        monkeypatch.setenv(name, "sk-should-never-reach-the-shell")

    env = shell_env()

    assert "sk-should-never-reach-the-shell" not in env.values()
    assert not [k for k in env if any(w in k for w in ("KEY", "TOKEN", "SECRET", "PASSWORD"))]
    # Anything added to the allowlist later must be justified as non-secret.
    assert set(env) <= set(SHELL_ENV_PASSTHROUGH)


@pytest.mark.unit
def test_shell_env_provides_what_git_and_gh_need(monkeypatch):
    """HOME and PATH are the two that break tooling when missing."""
    from src.agents.backend_wrapper import shell_env

    monkeypatch.setenv("HOME", "/home/someone")
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    env = shell_env()

    assert env["HOME"] == "/home/someone", "no HOME → gh reports itself as not logged in"
    assert env["PATH"] == "/usr/local/bin:/usr/bin", "no PATH → gh is not found at all"
