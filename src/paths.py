"""Where things live on disk.

Two kinds of path, and conflating them is the bug this module exists to prevent:

**Bundled assets** — prompts, skills, templates. They ship *with the code*, so under
``uv tool install`` they live inside site-packages. ``__file__`` is the only way to find
them, and that stays true forever.

**User data** — config, ``.env``, sessions, connector dumps, the wiki. These belong to the
person, not the install. They must never be derived from ``__file__``: site-packages is
read-only in spirit and is replaced wholesale on upgrade, so anything of yours living
there would be destroyed.

The rule: ``package_root()`` for the first kind, ``user_root()`` for the second.

Functions:
    package_root()  — the installed code tree. Never moves.
    user_root()     — the user's data directory. Clone → repo; installed → ~/.any2wiki.
    config_path()   — user_root()/config.yaml
    env_path()      — user_root()/.env
    ensure_user_root() — user_root(), created if missing.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Marker that tells a source checkout apart from an installed package. Verified absent
#: from site-packages, so its presence means "this is the repo".
_CLONE_MARKER = "pyproject.toml"

#: Where an installed copy keeps user data.
_INSTALLED_HOME = ".any2wiki"

#: Override for both cases. Tests use it to get an isolated directory.
_HOME_ENV = "ANY2WIKI_HOME"


def package_root() -> Path:
    """The directory holding the installed code — ``src/`` in a clone.

    Use for anything that ships with the package. Never for user data.
    """
    return Path(__file__).resolve().parent


def user_root() -> Path:
    """The directory holding user data: config, ``.env``, sessions, connector dumps.

    Resolution, first match wins:

    1. ``ANY2WIKI_HOME`` — explicit override, and what tests use.
    2. The repo root, when running from a clone (``pyproject.toml`` is next to ``src/``).
    3. ``~/.any2wiki`` — an installed package.

    Case 2 is why this cannot just be ``Path(__file__).parents[1]``: that expression is
    the repo when cloned but **site-packages when installed**, which is precisely the
    directory user data must not land in.

    The path is not created here — see :func:`ensure_user_root`.
    """
    if override := os.environ.get(_HOME_ENV, "").strip():
        return Path(override).expanduser().resolve()

    repo_root = package_root().parent
    if (repo_root / _CLONE_MARKER).exists():
        return repo_root

    return Path.home() / _INSTALLED_HOME


def ensure_user_root() -> Path:
    """``user_root()``, created if it does not exist.

    Call before writing. A fresh install has no ``~/.any2wiki`` until something makes it.
    """
    root = user_root()
    root.mkdir(parents=True, exist_ok=True)
    return root


def config_path() -> Path:
    """``config.yaml`` — settings. Written by ``any2wiki setup`` / ``config set``."""
    return user_root() / "config.yaml"


def env_path() -> Path:
    """``.env`` — API keys. Written by ``any2wiki keys set``, never by ``config set``."""
    return user_root() / ".env"
