"""
Pluggable rendering for the agent stream loop.

`run_turn_stream_async` emits events (tokens, tool calls, HITL interrupts) through a
``Renderer`` instead of calling ``print`` directly. This keeps the streaming loop in one
place while letting different front-ends decide how to display it:

- ``DefaultRenderer`` — the original ``print()``-based behavior, used from notebooks/tests.
- ``RichRenderer`` (``src/cli/renderer.py``) — Rich + prompt_toolkit terminal UI for the CLI.

Auto-approve of HITL interrupts is per-renderer instance state (set via constructor or the
interactive ``yolo`` choice), replacing the old module-level global.
"""

from __future__ import annotations

import ast
import json
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Renderer(Protocol):
    """Front-end contract for the agent stream loop."""

    def on_turn_start(self) -> None:
        """The turn began — the agent is thinking but hasn't emitted output yet.

        Front-ends can show a transient "thinking" indicator here (transient means disappear once thinking ends); it should be torn
        down by the first ``on_token`` / ``on_tool_call`` / ``handle_interrupts`` call.
        """
        ...

    def on_token(self, text: str) -> None:
        """A chunk of assistant text arrived (stream as it comes)."""
        ...

    def on_tool_call(self, name: str, args: Any) -> None:
        """The agent invoked a tool."""
        ...

    def on_tool_result(self, name: str, content: str) -> None:
        """A tool returned its result. Front-ends may preview/collapse long output."""
        ...

    def on_turn_end(self) -> None:
        """The turn finished streaming (e.g. emit a trailing newline)."""
        ...

    def on_debug(self, message: str) -> None:
        """Internal diagnostic message; shown only when debug is enabled."""
        ...

    def handle_interrupts(self, interrupts: list) -> list[dict]:
        """Prompt the user for HITL decisions and return the decisions list."""
        ...


# Prompt key → HITL decision type. ``yolo`` is a UI-only shortcut for approve-all.
CHOICE_TYPE = {"a": "approve", "e": "edit", "r": "reject", "s": "respond"}
CHOICE_LABEL = {
    "a": "[a]pprove",
    "e": "[e]dit",
    "r": "[r]eject",        # reject + an optional reason sent to the model
    "s": "re[s]pond",       # answer on behalf of the tool (ask-user tools)
    "yolo": "[yolo]=approve all",
}


def choices_for(allowed: list[str]) -> list[str]:
    """Prompt keys offered for an action, derived from its ``allowed_decisions``.

    Only decisions the tool actually permits are shown (so a reject-only tool never
    offers edit). ``yolo`` is appended when approve is allowed.
    """
    keys = [k for k in ("a", "e", "r", "s") if CHOICE_TYPE[k] in (allowed or [])]
    if "approve" in (allowed or []):
        keys.append("yolo")
    return keys


def legend(choices: list[str]) -> str:
    """Human-readable one-liner for the offered choices, e.g. ``[a]pprove / [r]eject``."""
    return " / ".join(CHOICE_LABEL[c] for c in choices)


def build_decisions(interrupts, prompt_fn, edit_fn, message_fn, state: "_AutoApprove") -> list[dict]:
    """Shared HITL decision loop.

    For each requested action it offers only the decisions the tool allows
    (``choices_for``) and asks the front-end to resolve them:
    - ``prompt_fn(action, review_config, choices)`` → the chosen key (``a/e/r/s/yolo``)
    - ``edit_fn(action)`` → raw new-args string (for ``e``)
    - ``message_fn(action, kind)`` → free-text for ``r`` (reason, optional) and
      ``s`` (the reply returned as the tool result)

    Decision shapes match ``langchain ... human_in_the_loop``:
    ``approve`` / ``edit`` / ``reject`` (optional ``message``) / ``respond`` (``message``).
    ``state`` carries the session auto-approve flag so ``yolo`` sticks for the run.
    """
    interrupt_value = interrupts[0].value
    action_requests = interrupt_value["action_requests"]
    review_configs = interrupt_value["review_configs"]
    config_map = {cfg["action_name"]: cfg for cfg in review_configs}

    decisions: list[dict] = []
    for action in action_requests:
        review_config = config_map[action["name"]]

        if state.auto_approve:
            decisions.append({"type": "approve"})
            continue

        choices = choices_for(review_config.get("allowed_decisions"))
        choice = prompt_fn(action, review_config, choices)

        if choice == "yolo":
            state.auto_approve = True
            decisions.append({"type": "approve"})
        elif choice == "r":
            # Reject + (optional) reason — feedback to the model so it tries a
            # different approach, the tool is NOT executed.
            reason = (message_fn(action, "reject") or "").strip()
            decision = {"type": "reject"}
            if reason:
                decision["message"] = reason
            decisions.append(decision)
        elif choice == "s":
            # Respond — return the text as a successful tool result (ask-user tools).
            decisions.append({"type": "respond", "message": message_fn(action, "respond") or ""})
        elif choice == "e":
            new_args = edit_fn(action)
            try:
                parsed = json.loads(new_args)
            except json.JSONDecodeError:
                # ast.literal_eval: parse Python literal syntax (e.g. {'a': 1}) safely—
                # no function calls or expressions, unlike eval().
                try:
                    parsed = ast.literal_eval(new_args)
                except (ValueError, SyntaxError):
                    decisions.append({"type": "approve"})
                    continue
            decisions.append({
                "type": "edit",
                "edited_action": {"name": action["name"], "args": parsed},
            })
        else:
            decisions.append({"type": "approve"})

    return decisions


class _AutoApprove:
    """Mutable holder for the session auto-approve flag (shared with build_decisions)."""

    def __init__(self, value: bool = False) -> None:
        self.auto_approve = value


class DefaultRenderer:
    """Original ``print()``-based behavior (notebook / non-CLI callers)."""

    def __init__(self, auto_approve: bool = False, debug: bool = False) -> None:
        self._state = _AutoApprove(auto_approve)
        self.debug = debug

    @property
    def auto_approve(self) -> bool:
        return self._state.auto_approve

    def on_turn_start(self) -> None:
        """No-op: the plain ``print`` front-end shows no thinking indicator."""

    def on_token(self, text: str) -> None:
        print(text, end="", flush=True)

    def on_tool_call(self, name: str, args: Any) -> None:
        args_preview = str(args)[:80]
        print(f"\n🔧 {name}({args_preview})", flush=True)

    def on_tool_result(self, name: str, content: str) -> None:
        """Print the full tool result inline (preserves notebook/test behavior)."""
        print(content, flush=True)

    def on_turn_end(self) -> None:
        print()

    def on_debug(self, message: str) -> None:
        if self.debug:
            print(message)

    def handle_interrupts(self, interrupts: list) -> list[dict]:
        def prompt_fn(action, review_config, choices):
            print(f"\n🔔 Tool: {action['name']}")
            print(f"   Args: {action['args']}")
            return input(f"{legend(choices)}: ").lower()

        def edit_fn(action):
            return input("New args (JSON or Python dict): ")

        def message_fn(action, kind):
            if kind == "reject":
                return input("Reason to send the model (optional): ")
            return input("Reply to return as the tool result: ")

        return build_decisions(interrupts, prompt_fn, edit_fn, message_fn, self._state)
