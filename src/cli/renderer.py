"""Rich + prompt_toolkit renderer for the CLI front-end.

Implements the ``Renderer`` protocol from ``src/agents/renderer.py``:
- assistant text streams as live-updating Markdown via ``rich.live.Live``
- tool calls render as styled lines
- HITL interrupts render as a panel; the choice is read with ``rich.prompt.Prompt`` (validated
  choices + styling). ``Prompt.ask`` blocks on stdin, which is fine even though
  ``handle_interrupts`` runs inside the active event loop — unlike prompt_toolkit's sync
  ``prompt()``, it does not start its own loop. Between-turn REPL input uses prompt_toolkit's
  async API instead — see ``src/cli/commands/chat.py``.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.text import Text

from src.agents.renderer import _AutoApprove, build_decisions


class RichRenderer:
    """Terminal renderer with streamed Markdown and styled HITL prompts."""

    def __init__(self, auto_approve: bool = False, debug: bool = False,
                 console: Console | None = None) -> None:
        self.console = console or Console()
        self.debug = debug
        self._state = _AutoApprove(auto_approve)
        self._live: Live | None = None
        self._buffer = ""

    @property
    def auto_approve(self) -> bool:
        return self._state.auto_approve

    # --- streaming ---------------------------------------------------------

    def on_token(self, text: str) -> None:
        if self._live is None:
            self._buffer = ""
            self._live = Live(console=self.console, refresh_per_second=12,
                              vertical_overflow="visible")
            self._live.start()
        self._buffer += text
        self._live.update(Markdown(self._buffer))

    def _end_live(self) -> None:
        """Finalize the current streamed block (if any) and leave it on screen."""
        if self._live is not None:
            self._live.update(Markdown(self._buffer))
            self._live.stop()
            self._live = None
            self._buffer = ""

    def on_tool_call(self, name: str, args: Any) -> None:
        self._end_live()
        args_preview = str(args)[:80]
        self.console.print(f"🔧 {name}({args_preview})", style="cyan")

    def on_turn_end(self) -> None:
        self._end_live()

    def on_debug(self, message: str) -> None:
        if self.debug:
            self.console.print(message, style="dim")

    # --- HITL --------------------------------------------------------------

    def handle_interrupts(self, interrupts: list) -> list[dict]:
        self._end_live()

        def prompt_fn(action, review_config):
            body = Text()
            body.append("Tool: ", style="bold")
            body.append(f"{action['name']}\n")
            body.append("Args: ", style="bold")
            body.append(f"{action['args']}\n")
            body.append("Allowed: ", style="bold")
            body.append(str(review_config["allowed_decisions"]))
            self.console.print(Panel(body, title="🔔 Approval required",
                                     border_style="yellow"))
            # choices map to build_decisions(): a=approve, e=edit, r=reject, yolo=approve-all.
            return Prompt.ask(
                "[bold]Decision[/]",
                choices=["a", "e", "r", "yolo"],
                default="a",
                console=self.console,
            )

        def edit_fn(action):
            return Prompt.ask("New args (JSON or Python dict)", console=self.console)

        return build_decisions(interrupts, prompt_fn, edit_fn, self._state)
