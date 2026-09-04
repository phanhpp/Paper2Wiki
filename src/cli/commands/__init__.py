"""One module per command.

Each module holds the Typer options next to the function, and delegates the real work
elsewhere (``fetch`` → ``src/connectors/``, ``serve`` → ``src/slack/``). ``chat.py`` is
the exception: the REPL input loop genuinely lives there.
"""
