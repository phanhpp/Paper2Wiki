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
    """

    paths: list[str]
    writes: list[str]           # wiki-relative paths written this run
    reads: list[str]            # paths read this run (from awrap_tool_call)
    tools: list[str]            # tool names called this run
    question: str               # the user's message for this turn
    answer: str                 # last AI message, flattened to text
    wiki_root: Path
    artifacts: list[str] = field(default_factory=list)   # repo-relative, outside the wiki
    artifacts_root: Path | None = None                   # e.g. <repo>/marp-slides

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
