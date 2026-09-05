"""The one place ``.env`` is read.

Only *entry points* call :func:`load_env` — the CLI callback, the eval scripts,
a notebook's first cell. Library modules under ``src/`` must never call it at
import time.

Why that rule exists: ``import src.agents.llms`` used to run ``load_dotenv()``
as a side effect, so merely collecting a test file that imported the agent put
``LANGSMITH_API_KEY`` and ``LANGSMITH_TRACING=true`` into the environment for
the whole pytest process. Unit tests then traced to the live LangSmith project.
Importing a module should not reconfigure the process.
"""

from __future__ import annotations

from pathlib import Path

from src.paths import user_root

from dotenv import load_dotenv

# .env is user data — see src/paths.py for why this is not derived from __file__.
REPO_ROOT = user_root()


def load_env(*, override: bool = False) -> None:
    """Load ``<repo>/.env`` into ``os.environ``.

    Safe to call more than once. ``override=False`` (the default) means a value
    already exported in the shell — or set by CI — wins over the file.
    """
    load_dotenv(REPO_ROOT / ".env", override=override)
