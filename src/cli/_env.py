"""Setup every command does before it starts: logging, flags, credential checks.

Each command begins with the same three lines::

    setup_logging(debug)
    apply_env(ingest_mode, wiki_path)
    require_keys(eval_mode)

**Order matters.** ``apply_env`` turns flags into env vars, and ``src/tools/__init__.py``
reads those env vars at *import* time to decide which tools exist. So the agent must not
be imported until after ``apply_env`` has run — which is why command bodies import it
lazily, part-way down the function instead of at the top of the file.

Functions:
    setup_logging(debug)         — quiet by default; --debug shows diagnostics.
    apply_env(mode, wiki_path)   — turn CLI flags into the env vars the app reads.
    require_keys(...)            — stop early, with a clear message, if a key is missing.
    _required_model_key()        — which provider key the configured model needs.
    _infer_provider(model)       — guess the provider from a model name.
"""

from __future__ import annotations

import logging
import os
from enum import Enum

import typer


class IngestMode(str, Enum):
    fast = "fast"
    quality = "quality"


def setup_logging(debug: bool = False) -> None:
    """Configure console logging for the CLI.

    Default: only WARNING+ reaches the console, so startup stays quiet. ``--debug``
    drops the level to DEBUG and surfaces the agent/tool diagnostics (Daytona build,
    provider routing, session save, etc.). Third-party loggers are pinned to WARNING
    unless ``--debug`` so their chatter doesn't leak in.
    """
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        force=True,  # take effect even if some import already configured the root logger
    )
    if not debug:
        for noisy in ("httpx", "httpcore", "urllib3", "anthropic", "openai", "daytona"):
            logging.getLogger(noisy).setLevel(logging.WARNING)


def apply_env(
    ingest_mode: IngestMode | None,
    wiki_path: str | None,
    model: str | None = None,
) -> None:
    """Turn CLI flags into the env vars the rest of the app already reads.

    Writing them into ``os.environ`` is what makes a flag beat ``config.yaml`` and
    ``.env``: every setting is looked up env-first, and this runs last. Nothing is
    written to disk, so the override lasts exactly one command.

    Args:
        ingest_mode: --ingest-mode, decides which ingest tools get registered.
        wiki_path: --wiki-path, where the wiki lives.
        model: --model, the base model for every task. A task-specific env var
            (``ANY2WIKI_MODEL_<TASK>``) still wins over this — see
            ``src/llm_roles.py``.
    """
    if ingest_mode is not None:
        os.environ["ANY2WIKI_INGEST_MODE"] = ingest_mode.value
    if wiki_path:
        os.environ["WIKI_PATH"] = wiki_path
    if model:
        os.environ["ANY2WIKI_MODEL"] = model


# Provider → the env var LangChain reads for it. Only providers we can name with
# confidence; an unlisted one is left for the provider SDK to complain about, since
# guessing wrong here would block a working setup (which is the bug this replaced).
_PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "azure_openai": "AZURE_OPENAI_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "groq": "GROQ_API_KEY",
    "mistralai": "MISTRAL_API_KEY",
    "cohere": "COHERE_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "together": "TOGETHER_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "xai": "XAI_API_KEY",
    # Deliberately absent: ollama (local, no key), google_vertexai (ADC, keyless).
}


def _infer_provider(model: str) -> str | None:
    """Guess which provider a model name belongs to.

    ``"openai:gpt-4o"`` says so outright, and that always wins. A bare name like
    ``"gpt-4o"`` has to be guessed from how it is spelled.

    Returns None when the name is not one we recognise, which means "don't check
    anything" — see ``_required_model_key``.
    """
    if ":" in model:
        return model.split(":", 1)[0]
    name = model.lower()
    if "claude" in name:
        return "anthropic"
    if name.startswith(("gpt-", "o1", "o3", "o4", "chatgpt")):
        return "openai"
    if name.startswith("gemini"):
        return "google_genai"
    return None


def _required_model_key() -> str | None:
    """Which API key env var does the configured model need? (None = don't check.)

    Two steps:

    1. Ask ``src/llm_roles.py`` which model is configured — that is whatever you put
       in ``ANY2WIKI_MODEL``, or in ``model.default`` in config.yaml.
    2. Work out which company provides that model, and return the name of the env var
       they read. An OpenAI model needs ``OPENAI_API_KEY``, a Gemini model needs
       ``GOOGLE_API_KEY``, and so on.

    Returns None — meaning check nothing — in four cases:

    * the key is written directly in config.yaml, so it isn't in the environment
    * a ``base_url`` is set, so some other endpoint handles auth (the LiteLLM
      gateway, Ollama, OpenRouter)
    * the provider needs no key at all (Ollama, Vertex AI)
    * we can't tell which provider it is

    **When unsure, return None.** Guessing wrong would refuse to start a setup that
    works — which is exactly the bug this function was written to fix. A missing key
    is a clear error from the provider a moment later; a false refusal is a dead end.
    """
    try:
        from src.llm_roles import get_model_spec

        spec = get_model_spec("supervisor")
    except Exception:
        # Config unreadable → llm_roles would fall back to Claude, so ask for its key.
        return "ANTHROPIC_API_KEY"

    if spec.api_key or spec.base_url:
        return None

    provider = spec.provider or _infer_provider(spec.model)
    return _PROVIDER_KEY_ENV.get(provider) if provider else None


def require_keys(eval_mode: bool, *, slack: bool = False) -> None:
    """Exit now, naming every missing key, rather than failing later mid-run.

    Three groups, each checked only when it is actually needed:

    ============================  ==========================================
    key                           needed when
    ============================  ==========================================
    the model provider's key      always — but *which* key depends on the
                                  model you configured, so an OpenAI setup is
                                  asked for ``OPENAI_API_KEY``, not
                                  Anthropic's. See ``_required_model_key``.
    ``DAYTONA_API_KEY``           unless ``--eval-mode``: it builds the
                                  sandbox the Marp subagent runs in.
    the three ``SLACK_*`` vars    only for ``serve``. Slack is an optional
                                  second way in, so every other command runs
                                  fine without them.
    ============================  ==========================================

    Args:
        eval_mode: True when the Daytona sandbox is skipped.
        slack: True for ``serve``, which also needs the Slack tokens.

    Raises:
        typer.Exit: code 1, after printing every missing variable at once.
    """
    missing = []
    model_key = _required_model_key()
    if model_key and not os.environ.get(model_key):
        missing.append(f"{model_key} (required by the configured model)")
    if not eval_mode and not os.environ.get("DAYTONA_API_KEY"):
        missing.append("DAYTONA_API_KEY (or pass --eval-mode to skip the Daytona sandbox)")
    if slack:
        # Workspace-scoped and per-user: each person creates their own Slack app.
        # The root README's "Slack (serve)" section walks through it — the two tokens
        # come from two different pages, which is the usual thing to get wrong.
        for var in ("SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "SLACK_CHANNEL_ID"):
            if not os.environ.get(var):
                missing.append(f"{var} (see README.md → Slack)")

    if missing:
        typer.secho(
            "Missing required environment variable(s):\n  - " + "\n  - ".join(missing)
            + "\nSet them in your .env (see .env.example).",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(1)
