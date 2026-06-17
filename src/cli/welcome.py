"""Rich welcome banner for the interactive REPL (Claude Code–style header).

Static, printed once at REPL start — no ``Live`` needed (the banner never changes).
Rendered via a single ``Panel`` so it stays one cohesive block above the
prompt_toolkit input loop. See ``docs/rich_header.md`` for the Rich primitives used.
"""

from __future__ import annotations

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

# A little robot mascot. Plain ASCII (note the [ and \ — can't use Rich markup here),
# styled green as a whole with the eyes recoloured black-on-green so they stay visible.
_BOT_ART = r""" __[\______/]__
|  _________   |
| | ◉  ‿  ◉ |  |
| |_________|  |
|______________|
  |__|    |__|"""


def _bot_text() -> Text:
    """Green robot with black-on-green eyes."""
    bot = Text(_BOT_ART, style="green")
    bot.highlight_words(["◕"], "green")  # eyes: black glyph, green fill
    return bot

def print_welcome(
    console: Console,
    *,
    thread_id: str,
    ingest_mode: str = "fast",
    no_save: bool = False,
    eval_mode: bool = False,
) -> None:
    """Print the Paper2Wiki welcome panel.

    The body reflects how the REPL actually works: you talk to the agent in plain
    language (ingest / query / self-improve), and ``/`` words are meta-commands.
    The footer shows the live session id + mode so it's obvious what you're in.
    """
    # Title: mix styles precisely with a Text object rather than markup.
    title = Text()
    title.append("Paper", style="bold green")
    title.append("2", style="bold white")
    title.append("Wiki", style="bold green")
    title.append("  ·  ", style="dim")
    title.append("self-improving agent", style="dim italic")

    # Commands listed vertically so each line reads on its own (sits to the
    # right of the mascot via the grid below).
    body = (
        "[bold]Talk to the agent in plain language[/]\n"
        "  [green]ingest[/] [dim]<arxiv id | url>[/]   build or extend the wiki\n"
        "  [green]ask[/] [dim]<question>[/]            query what the wiki knows\n"
        "  [green]analyze recent traces[/]       run self-improvement\n\n"
        "[bold]Meta-commands[/]\n"
        "  [cyan]/title[/] [dim]<name>[/]   name this session\n"
        "  [cyan]/new[/]            fresh session\n"
        "  [cyan]/help[/]           all commands\n"
        "  [cyan]/exit[/]           quit  [dim](quit · exit · bye · ^D)[/]\n"
        "  [cyan]/open[/]           last tool output  [dim](or ctrl-o)[/]"
    )

    # Footer: short session id + active modes, so the run's context is visible at a glance.
    parts = [f"[dim]session[/] {thread_id[:8]}…", f"[dim]{ingest_mode} mode[/]"]
    parts.append("[dim]no Daytona[/]" if eval_mode else "[dim]sonnet + haiku[/]")
    if no_save:
        parts.append("[yellow]--no-save[/]")
    subtitle = "  ·  ".join(parts)

    # Mascot on the left, command list vertically on the right — a borderless
    # two-column grid keeps them aligned side by side and top-anchored.
    content = Table.grid(padding=(0, 3))
    content.add_column(vertical="top")  # mascot
    content.add_column(vertical="top")  # commands
    content.add_row(_bot_text(), Text.from_markup(body))

    console.print(
        Panel(
            content,
            title=title,
            subtitle=subtitle,
            border_style="green",
            box=box.ROUNDED,   # soft corners, Claude Code look
            padding=(1, 3),
            expand=False,      # shrink to content instead of full width
        )
    )
    console.print()  # blank line before the first prompt
