"""The ``git-repo`` connector — a local repository as a source.

First connector on purpose: no auth, no network, fully deterministic, and it is
the data source for the code-wiki mode.

Two things it produces per repo:

- **what changed since last time** — commits and touched files, so a later run
  can update only the pages whose source moved
- **a fingerprint of the working tree** — see :func:`working_tree_fingerprint`

Cursor handling follows OpenWiki's ``sources/git-repo.ts``, which is more careful
than it first looks: the stored head is unusable in **three** cases, not one, and
all three fall back the same way.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Any, Iterator

from src.connectors.base import Item
from src.tools.hash_tools import compute_sha256

logger = logging.getLogger(__name__)

FIRST_RUN_COMMIT_LIMIT = 20   # bound the first run; a decade-old repo would dump everything
MAX_TREE_FILES = 2000


def _git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` and return stdout. Raises on a non-zero exit."""
    out = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()


def _reachable(repo: Path, sha: str) -> str | None:
    """Return ``sha`` if it still resolves to a commit here, else None.

    ``^{commit}`` matters: it asserts the object *is* a commit, not merely that
    some object with that hash exists. Without it a rewritten history can slip
    through and the later diff fails.
    """
    try:
        _git(repo, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
        return sha
    except subprocess.CalledProcessError:
        return None


def working_tree_fingerprint(repo: Path) -> str:
    """Hash HEAD *plus* the content of every tracked and untracked file.

    Comparing HEAD alone is wrong: uncommitted edits don't move HEAD, so a page
    whose source you just edited would look current. Ignore-filtered (git does
    that for us) so `.venv` churn doesn't make every run look dirty, sorted for
    stability, and length-delimited so two files can't concatenate into the same
    digest.
    """
    head = _git(repo, "rev-parse", "HEAD")
    files = sorted(
        _git(repo, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    )
    parts = [f"head:{head}"]
    for rel in files:
        path = repo / rel
        try:
            data = path.read_bytes()
        except OSError:
            continue
        parts.append(f"{len(rel)}:{rel}:{len(data)}:{compute_sha256.func(data.decode('utf-8', 'replace'))}")
    return f"sha256:{compute_sha256.func(chr(10).join(parts))}"


def _repo_snapshot(repo: Path, previous_head: str | None) -> dict[str, Any]:
    """Everything we record for one repo at one moment."""
    branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    head = _git(repo, "rev-parse", "HEAD")

    # Unusable in three cases, all falling back to the working tree:
    #   1. first run          — previous_head is None
    #   2. nothing new        — previous_head == head
    #   3. history rewritten  — force-push or gc, so it no longer resolves
    usable = (
        _reachable(repo, previous_head)
        if previous_head and previous_head != head
        else None
    )

    if usable:
        commits = _git(repo, "log", "--name-status", "--oneline", f"{usable}..HEAD")
        changed = _git(repo, "diff", "--name-status", usable, "HEAD")
    else:
        commits = _git(repo, "log", f"--max-count={FIRST_RUN_COMMIT_LIMIT}",
                       "--name-status", "--oneline")
        changed = _git(repo, "diff", "--name-status", "HEAD")   # uncommitted work

    tree = _git(repo, "ls-files", "--cached", "--others", "--exclude-standard").splitlines()

    return {
        "branch": branch,
        "head": head,
        "previous_head": usable,
        "incremental": bool(usable),
        "fingerprint": working_tree_fingerprint(repo),
        # `--name-status` is also where deletions show up, as `D` lines.
        "recent_commits": [c for c in commits.splitlines() if c],
        "changed_files": [c for c in changed.splitlines() if c],
        "file_tree": tree[:MAX_TREE_FILES],
        "file_tree_truncated": len(tree) > MAX_TREE_FILES,
    }


class GitRepoConnector:
    """Yields one item per configured repository."""

    name = "git-repo"

    def fetch(self, config: dict[str, Any], cursor: str | None) -> Iterator[Item]:
        """One ``Item`` per repo. ``cursor`` is the head we last recorded.

        A single shared cursor across repos is wrong when more than one is
        configured, so each repo's head rides on its own item and the ledger
        keeps the last one written. With one repo — the normal case — that is
        exactly right; multi-repo incremental needs per-repo cursors, noted in
        the README.
        """
        repos = config.get("repos") or []
        if not repos:
            logger.warning("git-repo: no repos configured; nothing to fetch")
            return

        for entry in repos:
            path = Path(entry["path"]).expanduser().resolve()
            repo_id = entry.get("id") or path.name
            if not (path / ".git").exists():
                raise ValueError(f"not a git repository: {path}")

            snapshot = _repo_snapshot(path, cursor)
            snapshot["repo_id"] = repo_id
            snapshot["path"] = str(path)

            yield Item(
                id=repo_id,
                source_url=f"file://{path}",
                payload=snapshot,
                cursor=snapshot["head"],
            )
