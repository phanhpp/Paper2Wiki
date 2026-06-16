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

import sys
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.spinner import Spinner
from rich.text import Text

import click

from src.agents.renderer import _AutoApprove, build_decisions, legend

try:  # POSIX-only; absent on Windows
    import termios
except ImportError:  # pragma: no cover - platform dependent
    termios = None  # type: ignore[assignment]


def _ctrl_stdin_fd() -> int | None:
    """stdin's fd if it's an interactive terminal whose attrs we can change, else None."""
    # If we are on Windows, termios is None. We can't use this, so abort.
    if termios is None:
        return None
    try:
        # Check if Python is running in a real terminal (True) or a background pipeline/file redirection (False).
        if not sys.stdin.isatty():
            return None
        # Return the system file descriptor integer for standard input (usually 0).
        return sys.stdin.fileno()
    except (ValueError, OSError):  # Catch issues if stdin was detached or closed entirely
        return None


class RichRenderer:
    """Terminal renderer with streamed Markdown and styled HITL prompts."""

    def __init__(self, auto_approve: bool = False, debug: bool = False,
                 console: Console | None = None) -> None:
        self.console = console or Console()
        self.debug = debug
        self._state = _AutoApprove(auto_approve)
        self._live: Live | None = None       # Live #2: streaming Markdown
        self._spinner: Live | None = None     # Live #1: transient "Thinking…"
        self._buffer = ""
        self._saved_term_attrs: list | None = None
        # Full tool outputs from the current turn, kept so Ctrl-O can page them
        # after the (truncated) inline preview. Reset each turn in on_turn_start.
        self._last_tool_output: list[tuple[str, str]] = []

    # Inline tool-result preview size before truncation.
    _PREVIEW_LINES = 6
    _PREVIEW_CHARS = 400

    @property
    def auto_approve(self) -> bool:
        return self._state.auto_approve

    # --- terminal echo (anti-smear) ----------------------------------------

    def _suppress_echo(self) -> None:
        """Stop the terminal echoing keystrokes while a response is streaming.

        Rich's ``Live`` repaints the streamed block ~12×/sec. If the user types ahead
        during that window, the terminal echoes each key onto the very lines Live is
        rewriting, smearing the output (the "take take take" artifact). Disabling ECHO
        for the streaming window keeps type-ahead invisible; it's still buffered and
        shows up cleanly at the next prompt once echo is restored.
        """
        fd = _ctrl_stdin_fd()
        if fd is None or self._saved_term_attrs is not None:
            return
        try:
            attrs = termios.tcgetattr(fd)
            self._saved_term_attrs = attrs
            new = list(attrs)
            new[3] &= ~termios.ECHO  # index 3 == lflags
            termios.tcsetattr(fd, termios.TCSANOW, new)
        except termios.error:  # pragma: no cover - terminal refused; leave echo as-is
            self._saved_term_attrs = None

    def restore_echo(self) -> None:
        """Re-enable terminal echo (idempotent; safe to call when never suppressed)."""
        fd = _ctrl_stdin_fd()
        if fd is None or self._saved_term_attrs is None:
            return
        try:
            termios.tcsetattr(fd, termios.TCSANOW, self._saved_term_attrs)
        except termios.error:  # pragma: no cover
            pass
        finally:
            self._saved_term_attrs = None

    # --- streaming ---------------------------------------------------------
    #
    # Two non-overlapping ``Live`` regions drive a turn (Rich allows only one Live
    # at a time, so they hand off rather than coexist):
    #
    #   on_turn_start()  → Live #1: a transient "Thinking…" spinner (no output yet)
    #   on_token() [1st] → stop Live #1, start Live #2 (streaming Markdown)
    #   on_token() [N]   → grow the Markdown buffer in Live #2
    #   on_turn_end()    → _end_live(): stop Live #2, leaving the text on screen
    #
    # Any event that produces real output (a token, a tool-call line, an HITL panel)
    # first tears the spinner down via _stop_spinner().

    def on_turn_start(self) -> None:
        """Show the transient "Thinking…" spinner while the agent works silently.

        Called at the top of every stream iteration (initial turn and each post-HITL
        resume), covering the gap before the first token or tool call. Echo is
        suppressed now so type-ahead can't smear the spinner; it stays suppressed
        through the Markdown stream and is restored in ``_end_live``.
        """
        self._last_tool_output = []  # Ctrl-O opens only this turn's tool output
        if self._spinner is not None:
            return
        self._suppress_echo()
        self._spinner = Live(
            Spinner("dots", text=Text(" Thinking…", style="dim")),
            console=self.console,
            refresh_per_second=12,
            transient=True,  # vanish on stop so it leaves no trace before output
        )
        self._spinner.start()

    def _stop_spinner(self) -> None:
        """Tear down the thinking spinner (Live #1), if running. Idempotent."""
        if self._spinner is not None:
            self._spinner.stop()
            self._spinner = None

    def on_token(self, text: str) -> None:
        """Append a streamed text chunk to the live Markdown block (Live #2).

        The first call hands off from the spinner: stop Live #1, then open Live #2.
        Subsequent calls just grow the buffer and re-render it.

        self._buffer is a plain Python string used as a cumulative accumulation tank.
        Every single time a new token arrives via on_token(text), the code appends it to the buffer.  
        _buffer glue all the tokens together into a single string.
        """
        if self._live is None:
            self._stop_spinner()  # swap spinner → Markdown stream
            self._buffer = ""
            self._suppress_echo()
            # transient + crop: while streaming, only the latest screenful is shown
            # in place (never taller than the terminal, so it can't smear/repeat).
            # _end_live clears it and prints the full Markdown once into scrollback.
            self._live = Live(console=self.console, refresh_per_second=12,
                              transient=True, vertical_overflow="crop")
            self._live.start() # 	Rich takes control of the terminal - open the canvas
        self._buffer += text
        self._live.update(Markdown(self._buffer)) # push content to the canvas

    def _end_live(self) -> None:
        """Finalize the streamed Markdown block (if any), leaving it on screen.

        Also clears the spinner and restores echo, so it's the single safe teardown
        for any end-of-turn / pre-prompt transition.
        """
        self._stop_spinner()
        if self._live is not None:
            self._live.stop()  # transient → erases the in-place streaming view
            self._live = None
            if self._buffer:
                # Commit the complete Markdown once; it scrolls normally (no smear).
                self.console.print(Markdown(self._buffer))
            self._buffer = ""
        self.restore_echo()

    def on_tool_call(self, name: str, args: Any) -> None:
        """Print a styled tool-call line, ending any spinner/stream above it first."""
        self._end_live()
        args_preview = str(args)[:80]
        self.console.print(f"🔧 {name}({args_preview})", style="cyan")

    def on_tool_result(self, name: str, content: str) -> None:
        """Show a short, dim preview of a tool result; stash the full text.

        Tool results (e.g. a 1000-line ``read_file``) are not streamed as Markdown —
        that smears the live view. Instead we print the first few lines and keep the
        whole thing in ``_last_tool_output`` so ``open_last_tool_output`` (Ctrl-O at
        the prompt) can page it.
        """
        self._end_live()
        self._last_tool_output.append((name, content))

        lines = content.splitlines()
        preview = lines[: self._PREVIEW_LINES]
        body = "\n".join(preview)[: self._PREVIEW_CHARS]
        for line in body.splitlines():
            self.console.print(f"  [dim]│ {line}[/]", highlight=False)

        hidden = len(lines) - len(preview)
        truncated = hidden > 0 or len(body) < len("\n".join(preview))
        if truncated:
            more = f"+{hidden} more lines · " if hidden > 0 else ""
            self.console.print(f"  [dim]… {more}ctrl-o to open full[/]")

    def open_last_tool_output(self) -> None:
        """Page the full tool output(s) from the most recent turn (Ctrl-O handler)."""
        if not self._last_tool_output:
            self.console.print("[dim](no tool output to open)[/]")
            return
        blocks = [f"── {name} ──\n{content}" for name, content in self._last_tool_output]
        click.echo_via_pager("\n\n".join(blocks))

    def on_turn_end(self) -> None:
        """Turn finished: finalize the streamed block and restore the terminal."""
        self._end_live()

    def on_debug(self, message: str) -> None:
        if self.debug:
            self.console.print(message, style="dim")

    # --- HITL --------------------------------------------------------------

    def handle_interrupts(self, interrupts: list) -> list[dict]:
        self._end_live()

        def prompt_fn(action, review_config, choices):
            body = Text()
            body.append("Tool: ", style="bold")
            body.append(f"{action['name']}\n")
            body.append("Args: ", style="bold")
            body.append(f"{action['args']}\n")
            body.append("Options: ", style="bold")
            body.append(legend(choices))  # only what this tool allows
            self.console.print(Panel(body, title="🔔 Approval required",
                                     border_style="yellow"))
            # keys → build_decisions(): a=approve, e=edit, r=reject(+reason),
            # s=respond, yolo=approve-all.
            return Prompt.ask(
                "[bold]Decision[/]",
                choices=choices,
                default="a" if "a" in choices else choices[0],
                console=self.console,
            )

        def edit_fn(action):
            return Prompt.ask("New args (JSON or Python dict)", console=self.console)

        def message_fn(action, kind):
            label = ("Reason to send the model (optional)" if kind == "reject"
                     else "Reply to return as the tool result")
            return Prompt.ask(label, default="", console=self.console)

        return build_decisions(interrupts, prompt_fn, edit_fn, message_fn, self._state)
