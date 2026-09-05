"""User data must never resolve into the installed package directory.

`Path(__file__).parents[1]` is the repo when cloned but **site-packages when installed** —
a tree that is replaced wholesale on upgrade. Anything of the user's living there is
destroyed by the next `uv tool install`. These tests pin the split.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src import paths


@pytest.mark.unit
def test_package_root_is_the_code_directory():
    assert paths.package_root() == Path(paths.__file__).resolve().parent
    assert (paths.package_root() / "paths.py").exists()


@pytest.mark.unit
def test_clone_resolves_user_root_to_the_repo(tmp_path, monkeypatch):
    """A checkout keeps writing where it always did — this is a seam, not a migration."""
    repo = tmp_path / "checkout"
    (repo / "src").mkdir(parents=True)
    (repo / "pyproject.toml").touch()

    monkeypatch.delenv(paths._HOME_ENV, raising=False)
    monkeypatch.setattr(paths, "package_root", lambda: repo / "src")

    assert paths.user_root() == repo


@pytest.mark.unit
def test_installed_resolves_user_root_to_home(tmp_path, monkeypatch):
    """The branch a clone can never reach: no pyproject.toml beside the package."""
    site_packages = tmp_path / "site-packages" / "src"
    site_packages.mkdir(parents=True)          # deliberately no pyproject.toml

    monkeypatch.delenv(paths._HOME_ENV, raising=False)
    monkeypatch.setattr(paths, "package_root", lambda: site_packages)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    root = paths.user_root()
    assert root == tmp_path / "home" / ".any2wiki"
    assert site_packages not in root.parents, "user data must not land in site-packages"


@pytest.mark.unit
def test_env_override_wins_over_both(tmp_path, monkeypatch):
    monkeypatch.setenv(paths._HOME_ENV, str(tmp_path / "elsewhere"))
    assert paths.user_root() == (tmp_path / "elsewhere").resolve()


@pytest.mark.unit
def test_override_moves_user_data_but_not_bundled_assets(tmp_path, monkeypatch):
    """The whole point of the split: one moves, the other cannot."""
    before = paths.package_root()
    monkeypatch.setenv(paths._HOME_ENV, str(tmp_path))

    assert paths.user_root() == tmp_path.resolve()
    assert paths.package_root() == before


@pytest.mark.unit
@pytest.mark.parametrize("fn,name", [(paths.config_path, "config.yaml"), (paths.env_path, ".env")])
def test_derived_paths_sit_under_user_root(tmp_path, monkeypatch, fn, name):
    monkeypatch.setenv(paths._HOME_ENV, str(tmp_path))
    assert fn() == tmp_path.resolve() / name


@pytest.mark.unit
def test_ensure_user_root_creates_a_missing_tree(tmp_path, monkeypatch):
    """A first run on a fresh install has no ~/.any2wiki — it must not crash."""
    target = tmp_path / "deep" / "not" / "there"
    monkeypatch.setenv(paths._HOME_ENV, str(target))

    assert not target.exists()
    assert paths.ensure_user_root() == target.resolve()
    assert target.is_dir()


@pytest.mark.unit
def test_ensure_user_root_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv(paths._HOME_ENV, str(tmp_path))
    paths.ensure_user_root()
    paths.ensure_user_root()            # must not raise on an existing directory


@pytest.mark.unit
def test_no_user_data_module_derives_a_root_from_file():
    """The regression guard: a new user-data path must go through user_root().

    `fetch_traces.py` used to resolve `trace_offloads/` with `__file__`, writing user data
    *inside* the package — already broken for an installed copy. Adding another such site
    should fail here, not on someone's first `uv tool install`.
    """
    import src.paths as paths_mod

    user_data_modules = [
        "src/env.py",
        "src/connectors/base.py",
        "src/middleware/wiki_rubric.py",
        "src/tools/utils.py",
        "src/sessions/utils.py",
        "src/tools/observability_eval_tools/fetch_traces.py",
    ]
    import ast

    repo = Path(paths_mod.__file__).resolve().parents[1]

    # AST, not grep: these files *mention* __file__ in comments explaining why they no
    # longer use it, and a textual search would flag its own documentation.
    offenders = []
    for rel in user_data_modules:
        tree = ast.parse((repo / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "__file__":
                offenders.append(f"{rel}:{node.lineno}")

    assert not offenders, (
        "these resolve a root from __file__ but hold user data — use "
        "src.paths.user_root() instead:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.unit
def test_config_reader_and_writers_agree(tmp_path, monkeypatch):
    """`_find_config_path()` must resolve the same file `config_path()` writes.

    They used to disagree: the reader looked in `~/.paper2wiki/`, while `.env` lived in
    the repo. A setup command writing to one and a reader looking at the other is a
    confusing bug to chase.
    """
    from src.tools.web_tools.registry import _find_config_path

    monkeypatch.setenv(paths._HOME_ENV, str(tmp_path))
    monkeypatch.delenv("ANY2WIKI_CONFIG", raising=False)
    paths.config_path().write_text("model:\n  default: from-user-root\n")

    assert _find_config_path() == paths.config_path()
