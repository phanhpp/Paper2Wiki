"""`git-repo` against real temporary repositories.

No mocking: git is local, deterministic and fast, so a real `git init` tests the
thing we actually ship. Mocking `subprocess` here would only test our mock.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.connectors.base import read_manifest, run_fetch
from src.connectors.git_repo import GitRepoConnector, working_tree_fingerprint


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A repo with two commits."""
    path = tmp_path / "demo"
    path.mkdir()
    _git(path, "init", "-q", ".")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / "a.txt").write_text("first\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "first commit")
    (path / "b.txt").write_text("second\n")
    _git(path, "add", "-A")
    _git(path, "commit", "-qm", "second commit")
    return path


def _config(repo: Path) -> dict:
    return {"repos": [{"id": "demo", "path": str(repo)}]}


@pytest.mark.unit
def test_first_run_captures_the_repo(repo: Path, tmp_path: Path):
    result = run_fetch(GitRepoConnector(), _config(repo), tmp_path / "out")

    assert result.new == 1
    manifest = read_manifest("git-repo", tmp_path / "out")
    assert manifest["cursor"] == _git(repo, "rev-parse", "HEAD")

    import json
    payload = json.loads((tmp_path / "out" / "git-repo" / "raw" / "demo.json").read_text())
    assert payload["incremental"] is False            # first run: no usable cursor
    assert set(payload["file_tree"]) == {"a.txt", "b.txt"}
    assert len(payload["recent_commits"]) > 0


@pytest.mark.unit
def test_second_run_with_no_commits_is_unchanged(repo: Path, tmp_path: Path):
    out = tmp_path / "out"
    run_fetch(GitRepoConnector(), _config(repo), out)
    result = run_fetch(GitRepoConnector(), _config(repo), out)

    assert (result.new, result.unchanged) == (0, 1)


@pytest.mark.unit
def test_a_new_commit_moves_the_cursor(repo: Path, tmp_path: Path):
    out = tmp_path / "out"
    run_fetch(GitRepoConnector(), _config(repo), out)
    first_head = read_manifest("git-repo", out)["cursor"]

    (repo / "c.txt").write_text("third\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "third commit")

    result = run_fetch(GitRepoConnector(), _config(repo), out)

    assert result.new == 1
    assert read_manifest("git-repo", out)["cursor"] != first_head


@pytest.mark.unit
def test_an_unreachable_stored_head_falls_back_instead_of_erroring(repo: Path, tmp_path: Path):
    """After a force-push or a gc, the recorded head can stop resolving.

    This is what OpenWiki's `rev-parse --verify ...^{commit}` guard exists for:
    without it the follow-up `git diff <gone-sha>..HEAD` fails outright and the
    connector is stuck until someone clears the manifest by hand.

    The stored head is set directly rather than by rewriting history, because
    `--orphan` leaves the old commits reachable via the previous branch and gc
    behaviour varies by git version. What matters is the condition — a head that
    no longer resolves — not how it got that way.
    """
    import json
    out = tmp_path / "out"
    run_fetch(GitRepoConnector(), _config(repo), out)

    manifest = read_manifest("git-repo", out)
    manifest["cursor"] = "0" * 40                      # well-formed, nonexistent
    (out / "git-repo" / "manifest.json").write_text(json.dumps(manifest))

    result = run_fetch(GitRepoConnector(), _config(repo), out)      # must not raise

    assert result.warnings == []
    payload = json.loads((out / "git-repo" / "raw" / "demo.json").read_text())
    assert payload["incremental"] is False          # fell back to the working tree
    assert payload["previous_head"] is None


@pytest.mark.unit
def test_fingerprint_changes_on_an_uncommitted_edit(repo: Path):
    """Why HEAD alone is not enough — the whole point of item 10."""
    head_before = _git(repo, "rev-parse", "HEAD")
    before = working_tree_fingerprint(repo)

    (repo / "a.txt").write_text("edited, not committed\n")

    assert _git(repo, "rev-parse", "HEAD") == head_before   # HEAD did not move
    assert working_tree_fingerprint(repo) != before          # but the fingerprint did


@pytest.mark.unit
def test_fingerprint_sees_a_new_untracked_file(repo: Path):
    before = working_tree_fingerprint(repo)
    (repo / "new.txt").write_text("untracked\n")
    assert working_tree_fingerprint(repo) != before


@pytest.mark.unit
def test_fingerprint_ignores_gitignored_churn(repo: Path):
    """Otherwise `.venv` noise makes every run look dirty."""
    (repo / ".gitignore").write_text("noise/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "add gitignore")

    before = working_tree_fingerprint(repo)
    (repo / "noise").mkdir()
    (repo / "noise" / "junk.txt").write_text("churn\n")

    assert working_tree_fingerprint(repo) == before


@pytest.mark.unit
def test_fingerprint_is_stable_across_calls(repo: Path):
    assert working_tree_fingerprint(repo) == working_tree_fingerprint(repo)


@pytest.mark.unit
def test_a_non_repo_path_is_rejected(tmp_path: Path):
    result = run_fetch(GitRepoConnector(),
                       {"repos": [{"id": "x", "path": str(tmp_path)}]},
                       tmp_path / "out")
    assert result.new == 0
    assert any("not a git repository" in w for w in result.warnings)


@pytest.mark.unit
def test_no_repos_configured_is_a_no_op(tmp_path: Path):
    result = run_fetch(GitRepoConnector(), {"repos": []}, tmp_path / "out")
    assert (result.new, result.warnings) == (0, [])
