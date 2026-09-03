"""Shared vocabulary for the Loop 2 verification middleware.

Deliberately free of LangChain/LangGraph imports so ``checks/`` and
``wiki_rubric.py`` can both depend on it without importing each other, and so
the whole check suite stays unit-testable with no agent and no graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

# Contracts a run can be held to. A run may match more than one
# (e.g. "ingest this paper and make slides").
Path_ = Literal["ingest", "query", "marp"]

Verdict = Literal[
    "no_path_matched",         # not a wiki run — the normal case, never reported
    "satisfied",               # every check passed
    "needs_revision",          # >=1 check failed, attempts remain
    "max_iterations_reached",  # >=1 check failed, retry cap hit
    "check_error",             # a check raised — our bug, not the agent's
]


@dataclass(frozen=True)
class RunContext:
    """Everything a check is allowed to look at, for one run.

    Assembled once in ``after_agent``. ``writes`` comes from diffing a
    filesystem snapshot rather than from tool calls, so pages written through
    any route are still seen — the shell, the sandbox download tool, or a tool
    added later that we know nothing about.

    File paths come in pairs: a list of *relative* paths, plus the directory they
    are relative to. A check joins the two to open a file::

        wiki_root      = /Users/you/llm_wiki/wiki
        writes         = ["concepts/attention.md"]
        open it with     wiki_root / "concepts/attention.md"

        artifacts_root = /Users/you/llm_wiki/marp-slides
        artifacts      = ["deck.md"]
        open it with     artifacts_root / "deck.md"

    They are relative because that is how the snapshots record them, which keeps
    them short and independent of where the repo lives.
    """

    paths: list[str]            # which contracts matched: ingest / query / marp

    # --- the wiki ---
    wiki_root: Path             # absolute path to wiki/
    writes: list[str]           # files written this run, relative to wiki_root

    # --- marp decks, which live outside the wiki ---
    artifacts_root: Path | None = None                   # absolute path to marp-slides/
    artifacts: list[str] = field(default_factory=list)   # files written, relative to artifacts_root

    # --- everything else ---
    reads: list[str] = field(default_factory=list)   # files read (from awrap_tool_call)
    tools: list[str] = field(default_factory=list)   # tool names called this run
    question: str = ""          # what the user asked this turn
    answer: str = ""            # the agent's reply, as plain text

    def wrote_under(self, *prefixes: str) -> list[str]:
        """Paths written this run that sit under any of ``prefixes``."""
        return [w for w in self.writes if w.startswith(prefixes)]

    def read_under(self, *prefixes: str) -> list[str]:
        """Paths read this run that sit under any of ``prefixes``."""
        return [r for r in self.reads if r.startswith(prefixes)]


@dataclass(frozen=True)
class CheckResult:
    """One check's verdict.

    Two consumers, which is why both fields exist: ``passed`` aggregates into
    the run verdict and into ``Evaluation.criteria``; ``gap`` becomes the retry
    message, so it must name the *specific* failure ("graph.json has no node
    for 'attention'"), never restate the rule.
    """

    id: str
    passed: bool
    gap: str | None = None

    @classmethod
    def ok(cls, check_id: str) -> "CheckResult":
        return cls(id=check_id, passed=True)

    @classmethod
    def fail(cls, check_id: str, gap: str) -> "CheckResult":
        return cls(id=check_id, passed=False, gap=gap)


# A check is any function of this shape. Keeping them uniform is what lets the
# middleware run CHECKS[path] without knowing what any individual check does.
Check = Callable[[RunContext], CheckResult]


@dataclass(frozen=True)
class Evaluation:
    """One grading pass, handed to ``on_evaluation`` and to the stream.

    Mirrors deepagents' ``RubricEvaluation`` so consumers of either look the
    same. All fields are always populated — no absence guards needed.
    """

    run_id: str | None
    iteration: int
    result: str
    explanation: str
    criteria: list[CheckResult] = field(default_factory=list)

    @property
    def failed(self) -> list[CheckResult]:
        return [c for c in self.criteria if not c.passed]
