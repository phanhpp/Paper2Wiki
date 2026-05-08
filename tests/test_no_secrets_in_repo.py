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
