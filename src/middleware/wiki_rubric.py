"""Checks the agent's wiki work before letting a run finish.

The flow, once per turn::

    before_agent      list every file in wiki/ and marp-slides/, and note
                      what the user asked
          |
    (agent works)     awrap_tool_call notes each tool it uses and each file
                      it reads
          |
    after_agent       list the files again and compare -> what it wrote
                      |
                      +- wrote entities/ or concepts/  -> ingest checks
                      +- wrote marp-slides/            -> marp checks
                      +- user asked to use the wiki    -> query checks
                      +- none of those                 -> stop, say nothing
                      |
                      +- all checks pass -> stop
                      +- something wrong -> send the agent back with the list
                                            of problems, up to max_iterations
                                            times, then report and stop

Why it exists: `quick_wiki_integrity_check` is a tool, so the model can skip it
or ignore what it says. This moves the decision out of the model's hands.

Recording a tool call — runs on every call::

    awrap_tool_call(request, handler)
        result = await handler(request)          the tool runs
              |
        _record(request, result)                 our notes for this call:
              |                                  {"run_tools": ["task"], ...}
              |
        _with_record(request, result)            put the notes somewhere safe
              |
              +-- result is a Message  ->  Command(update={notes, "messages":[result]})
              |
              +-- result is a Command  ->  replace(result, update={its update | notes})
                                           (only subagents; keeps its "messages",
                                            adds ours beside it)
              |
              v
        state: run_tools / run_reads  ---->  after_agent reads them to decide
                                             which checks to run

The bug this fixed: the first branch was used for both, so a subagent's Command
landed inside "messages" — every item there must be a Message, so LangGraph
raised `Unsupported message type: <class 'langgraph.types.Command'>`.

No AI model is used here — every check reads files and compares strings, so the
loop costs nothing. See this package's README for the checks themselves.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, replace
from pathlib import Path
from typing import Annotated, Any, Callable

from langchain.agents.middleware import AgentMiddleware, AgentState, hook_config
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from typing_extensions import NotRequired

from src.middleware.checks import checks_for
from src.middleware.classify import classify
from src.middleware.feedback import explain, feedback_text
from src.middleware.types import CheckResult, Evaluation, RunContext
from src.tools.utils import get_wiki_root

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
# Decks are built in the Daytona sandbox and downloaded here, outside the wiki
# (src/agents/daytona_agent.py:download_outputs), so it needs its own snapshot.
ARTIFACTS_DIR = REPO_ROOT / "marp-slides"

# Marks messages this middleware injects, so a later turn's classifier does not
# mistake our retry prompt for a user request. Mirrors deepagents'
# RubricMiddleware, which tags its own revision messages the same way.
FEEDBACK_SOURCE = "wiki_rubric"

# read_file names its file in the ARGUMENTS.
_ARG_READ_TOOLS = frozenset({"read_file"})
_PATH_ARGS = ("file_path",)

# grep/glob/ls name files in their RESULT instead — their `path` argument is the
# directory to search, so reading that would log "/wiki/" as a page. glob returns
# matching paths, grep returns matching paths (or whole lines with output_mode=
# "content"), so the agent really has seen those pages and citing one is not
# fabrication. Q2 would wrongly fail a search-then-cite run if we ignored these.
_RESULT_READ_TOOLS = frozenset({"grep", "glob", "ls"})
_MD_PATH_RE = re.compile(r"[\w./\-]+\.md")


def append_or_reset(prev: list[str], new: list[str] | None) -> list[str]:
    """Add to the list. Passing None empties it instead.

    Tools can run at the same time, so we let LangGraph merge the additions
    rather than each one reading the list and writing it back — two doing that
    at once would lose an entry.

    None is the "empty it" signal because adding an empty list adds nothing,
    which would leave last turn's entries in place.
    """
    return [] if new is None else (prev or []) + new


class WikiRubricState(AgentState):
    """What we remember about the current turn. Not visible to the agent.

    There is no list of files written. We work that out at the end by comparing
    the two snapshots below against what is on disk now. See the README for why.
    """

    run_reads: NotRequired[Annotated[list[str], append_or_reset]]   # files the agent read
    run_tools: NotRequired[Annotated[list[str], append_or_reset]]   # tools it called
    run_question: NotRequired[str]                                  # what the user asked

    # File listings taken before the agent ran, so we can spot what changed.
    wiki_snapshot: NotRequired[dict[str, str]]        # wiki/
    artifact_snapshot: NotRequired[dict[str, str]]    # marp-slides/

    attempts: NotRequired[int]                        # retries used this turn


def snapshot_dir(root: Path) -> dict[str, str]:
    """List every file under `root` with when it changed and how big it is.

    Returns something like {"index.md": "1754...:412"} — the number before the
    colon is the last-modified time, after it is the size in bytes.

    We include the size because log.md is added to rather than replaced. Two
    additions in the same instant would show the same time and look untouched;
    the size still changes, so we notice.
    """
    snap: dict[str, str] = {}
    if not root.is_dir():
        return snap
    for p in root.rglob("*"):
        if p.is_file():
            try:
                st = p.stat() # stat
            except OSError:
                continue
            snap[str(p.relative_to(root))] = f"{st.st_mtime_ns}:{st.st_size}"
    return snap


def diff_dir(before: dict[str, str], root: Path) -> list[str]:
    """Which files are new or changed since the `before` list was made.

    Take a listing before the agent runs, take another after, compare them.
    Anything that differs was written during the run — no matter whether the
    agent used write_file, a shell command, the sandbox download tool, or
    something added later — the files on disk are the files on disk.
    """
    after = snapshot_dir(root)
    return sorted(rel for rel, stamp in after.items() if before.get(rel) != stamp)


def last_user_question(messages: list[Any]) -> str:
    """What the user asked this turn.

    We look at the *last* message, not the first. A conversation keeps all its
    old messages, so the first one is always turn 1's question.

    We skip our own retry messages: they are sent as if from the user, but they
    are not what the user asked for.
    """
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            if m.additional_kwargs.get("lc_source") == FEEDBACK_SOURCE:
                continue
            return _text_of(m) # get last human message as plain text
    return ""


def _text_of(message: Any) -> str:
    """Get a message's text.

    Some providers return plain text, others return a list of pieces. This
    handles both.
    """
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return str(content)


def _last_ai_text(messages: list[Any]) -> str:
    """The agent's most recent reply, as text. This is the answer we check."""
    for m in reversed(messages):
        if isinstance(m, AIMessage):
            return _text_of(m)
    return ""


def _verification_config() -> dict[str, Any]:
    """Read our settings from config.yaml, or return {} if there are none.

    Never raises. A missing or broken config should fall back to defaults, not
    stop the agent from starting.
    """
    try:
        from src.tools.web_tools.registry import load_config_file

        block = (load_config_file() or {}).get("verification")
        return block if isinstance(block, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


class WikiRubricMiddleware(AgentMiddleware[WikiRubricState]):
    """Checks the agent's wiki work, and sends it back to fix mistakes.

    Three hooks:
      before_agent    - note what the wiki looks like before the agent starts
      awrap_tool_call - note each tool the agent uses
      after_agent     - see what changed, check it, retry if it is wrong
    """

    state_schema = WikiRubricState

    def __init__(
        self,
        max_iterations: int = 2,
        on_evaluation: Callable[[Evaluation], None] | None = None,
        enabled: bool = True,
    ) -> None:
        """
        Args:
            max_iterations: how many times to send the agent back before giving
                up and reporting the failure.
            on_evaluation: called with the result after every check run. This is
                how you find out a run failed — nothing is added to the
                conversation.
            enabled: set False to switch the whole thing off.
        """
        super().__init__()
        if not isinstance(max_iterations, int) or isinstance(max_iterations, bool):
            raise TypeError(
                f"max_iterations must be an int, got {type(max_iterations).__name__}"
            )
        if max_iterations < 1:
            raise ValueError(f"max_iterations must be positive, got {max_iterations}")
        self.max_iterations = max_iterations
        self._on_evaluation = on_evaluation
        self.enabled = enabled

    @classmethod
    def from_config(
        cls, on_evaluation: Callable[[Evaluation], None] | None = None
    ) -> "WikiRubricMiddleware":
        """Build one using the settings in config.yaml.

        Set `enabled: false` there to switch the whole thing off — useful if a
        check starts misfiring and you need it gone without changing code.
        """
        cfg = _verification_config()
        return cls(
            max_iterations=int(cfg.get("max_iterations", 2)),
            on_evaluation=on_evaluation,
            enabled=bool(cfg.get("enabled", True)),
        )

    # --- before_agent: once per turn, never on a retry ----------------------

    def before_agent(self, state: WikiRubricState, runtime) -> dict[str, Any] | None:
        """Get ready for a new turn: clear last turn's notes, list the files.

        Runs once when the turn starts, and — importantly — *not* again on a
        retry. If it did, it would wipe our notes half way through and the agent
        would look like it had done nothing.
        """
        if not self.enabled:
            return None
        return {
            "run_reads": None,          # None resets, see append_or_reset
            "run_tools": None,
            "run_question": last_user_question(state["messages"]),
            "wiki_snapshot": snapshot_dir(get_wiki_root()),
            "artifact_snapshot": snapshot_dir(ARTIFACTS_DIR),
            "attempts": 0,
        }

    async def abefore_agent(self, state: WikiRubricState, runtime) -> dict[str, Any] | None:
        """Async version. Same work, so it just calls the sync one."""
        return self.before_agent(state, runtime)

    # --- awrap_tool_call: tool names and reads ------------------------------

    def _record(self, request, result: Any = None) -> dict[str, Any]:
        """Write down the tool's name, and any files it showed the agent.

        Returns our notes for this one tool call, e.g.::

            {"run_tools": ["read_file"], "run_reads": ["wiki/index.md"]}

        We do this because reading a file changes nothing on disk, so comparing
        the wiki before and after would never reveal it. Only the tool call knows.

        Two ways a tool names a file:
          read_file        - in its arguments (file_path)
          grep/glob/ls     - in its result, as a list of matching paths

        Both count as the agent having seen the page, so both go in run_reads.

        ``run_tools`` matters on its own too: ``classify()`` treats the name
        ``task`` as proof a subagent ran, which is how the marp path is detected
        (``classify.py:MARP_TOOLS``). Lose it and a slide deck is never checked.
        """
        name = request.tool_call.get("name", "")
        update: dict[str, Any] = {"run_tools": [name]}

        if name in _ARG_READ_TOOLS:
            args = request.tool_call.get("args") or {}
            for key in _PATH_ARGS:
                if args.get(key):
                    update["run_reads"] = [str(args[key])]
                    break

        elif name in _RESULT_READ_TOOLS and result is not None:
            found = _MD_PATH_RE.findall(_text_of(result))
            if found:
                update["run_reads"] = sorted(set(found))

        return update

    def _with_record(self, request, result):
        """Attach our state update to whatever the tool returned.

        Two shapes to handle:

        *A normal result* (a ToolMessage) — wrap it in a Command, passing it back
        under "messages". Returning a Command replaces the normal return, so
        without that the model never sees what the tool said.

        *A Command* — some tools return one already; the subagent ``task`` tool
        does. Merge into its update instead of nesting it under "messages", which
        would hand a Command to the messages reducer and raise
        ``Unsupported message type: <class 'langgraph.types.Command'>``. Our keys
        (run_reads/run_tools) never collide with the tool's, so a shallow merge
        keeps both.
        """
        recorded = self._record(request, result)
        if isinstance(result, Command):
            if not isinstance(result.update, dict):
                # A non-dict update (rare) can't be merged safely — record
                # nothing rather than corrupt the tool's own return.
                return result
            return replace(result, update={**result.update, **recorded})
        return Command(update={**recorded, "messages": [result]})

    def wrap_tool_call(self, request, handler):
        """Run the tool, then write down what it was.

        The sync variant is only for tests — see ``awrap_tool_call``.
        """
        result = handler(request)
        if not self.enabled:
            return result
        return self._with_record(request, result)

    async def awrap_tool_call(self, request, handler):
        """Same as above, for async. This is the one that really runs.

        Our agent is async, and LangChain will not use the sync version as a
        backup — it raises an error instead.
        """
        result = await handler(request)
        if not self.enabled:
            return result
        return self._with_record(request, result)

    # --- after_agent: once per attempt, including each retry ----------------

    @hook_config(can_jump_to=["model"])
    def after_agent(self, state: WikiRubricState, runtime) -> dict[str, Any] | None:
        """The main event: see what the run did, check it, retry if it is wrong.

        Steps:
          1. compare the wiki now against the listing taken at the start,
             to get the files this run wrote
          2. work out what kind of run it was (ingest / query / marp / none)
          3. run that kind's checks
          4. all good, or out of tries -> report and stop
             something wrong -> send the agent back with the list of problems

        Returning `jump_to: "model"` is what sends it back. This hook then runs
        again on the retry, so the fix gets checked too.

        A run that touched nothing is left alone — no checks, no report. That is
        every ordinary chat message, and reporting on those would be noise.
        """
        if not self.enabled:
            return None

        wiki_root = get_wiki_root()
        writes = diff_dir(state.get("wiki_snapshot") or {}, wiki_root)
        artifacts = diff_dir(state.get("artifact_snapshot") or {}, ARTIFACTS_DIR)
        question = state.get("run_question", "")
        reads = state.get("run_reads") or []
        tools = state.get("run_tools") or []

        paths = classify(writes, reads, tools, question, artifacts)
        if not paths:
            return None  # no_path_matched — deliberately silent, no callback

        ctx = RunContext(
            paths=paths,
            writes=writes,
            reads=reads,
            tools=tools,
            question=question,
            answer=_last_ai_text(state["messages"]),
            wiki_root=wiki_root,
            artifacts=artifacts,
            artifacts_root=ARTIFACTS_DIR,
        )

        results, errored = self._run_checks(ctx)
        failed = [r for r in results if not r.passed]
        attempts = state.get("attempts", 0)

        if errored:
            verdict = "check_error"
        elif not failed:
            verdict = "satisfied"
        elif attempts >= self.max_iterations:
            verdict = "max_iterations_reached"
        else:
            verdict = "needs_revision"

        self._emit(runtime, verdict, attempts, results)

        if verdict != "needs_revision":
            # satisfied / max_iterations_reached / check_error all stop here.
            # The agent's own final output is returned unchanged; the verdict
            # reaches the caller through on_evaluation and the stream.
            return None

        return {
            "messages": [
                HumanMessage(
                    content=feedback_text(failed, ctx),
                    name=FEEDBACK_SOURCE,
                    additional_kwargs={"lc_source": FEEDBACK_SOURCE},
                )
            ],
            "attempts": attempts + 1,
            "jump_to": "model",
        }

    async def aafter_agent(self, state: WikiRubricState, runtime) -> dict[str, Any] | None:
        """Async version. The checks only read files, so no await is needed."""
        return self.after_agent(state, runtime)

    # --- internals ----------------------------------------------------------

    def _run_checks(self, ctx: RunContext) -> tuple[list[CheckResult], bool]:
        """Run all the checks that apply to this run.

        If a check crashes that is our bug, not the agent's, so we record it as
        a failure and keep going. One broken check should not bring down the
        whole agent.
        """
        results: list[CheckResult] = []
        errored = False
        for path in ctx.paths:
            for check in checks_for(path):
                try:
                    results.append(check(ctx))
                except Exception as exc:  # noqa: BLE001 — deliberately broad
                    errored = True
                    name = getattr(check, "__name__", repr(check))
                    logger.exception("wiki check %s raised", name)
                    results.append(CheckResult.fail(name, f"check raised: {exc!r}"))
        return results, errored

    def _emit(self, runtime, verdict: str, iteration: int, results: list[CheckResult]) -> None:
        """Report the result, so the caller knows how the run was graded.

        Sent two ways: the on_evaluation callback, and the event stream.

        This is the only place the result shows up. We never add a message to
        the conversation, so if you want to know the agent gave up after two
        tries, you have to listen here.

        Both sends are wrapped in try/except: someone else's callback crashing,
        or a runtime with no stream writer, must not kill the agent run.
        """
        evaluation = Evaluation(
            run_id=getattr(getattr(runtime, "execution_info", None), "run_id", None),
            iteration=iteration,
            result=verdict,
            explanation=explain(results),
            criteria=results,
        )

        if self._on_evaluation is not None:
            try:
                self._on_evaluation(evaluation)
            except Exception:  # noqa: BLE001 — a caller's callback must not crash the run
                logger.exception("on_evaluation callback failed")

        # Absent outside a streaming context — attribute access would crash.
        writer = getattr(runtime, "stream_writer", None)
        if writer is not None:
            try:
                writer({"type": "wiki_rubric_evaluation", **asdict(evaluation)})
            except Exception:  # noqa: BLE001
                logger.debug("stream_writer rejected wiki_rubric_evaluation", exc_info=True)
