"""The terminal front-end.

Turns the agent into commands you can type. Nothing here contains agent logic — it
parses flags, draws the terminal, and cleans up async resources.

Built on three libraries, each doing one job:
    Typer           — parses commands and flags
    Rich            — draws output (Markdown, panels, spinners, tables)
    prompt_toolkit  — reads the input line in the REPL

See README.md in this folder for the layout and the two rules worth knowing.
"""
