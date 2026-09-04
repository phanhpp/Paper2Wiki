import logging
import time
from deepagents import create_deep_agent, CompiledSubAgent
from src.tools import all_tools
from src.prompts.system_prompt import PHASE_1_SUPERVISOR_PROMPT
from langgraph.store.memory import InMemoryStore
import aiosqlite
from src.agents.backend_wrapper import GuardedLocalShellBackend, shell_env
# NOTE: create_daytona_agent (→ langchain_daytona → the Daytona SDK) is imported lazily inside
# create_supervisor, only when eval_mode is False. The import itself is ~3.6s; the real cost is at
# call time — create_daytona_agent provisions/restores a sandbox over the network (tens of seconds).
# Deferring keeps `import src.agents.agent` cheap and lets eval-mode runs skip Daytona entirely.
from src.agents.sandbox_utils import register_sandbox
from src.agents.llms import set_up_llms
from src.llm_roles import get_model_spec
from langchain_core.utils.uuid import uuid7
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from src.sessions.sessions_db_setup import SESSIONS_DIR
from pathlib import Path
from langchain.agents.middleware import PIIMiddleware, ModelCallLimitMiddleware, ToolCallLimitMiddleware
from src.middleware import WikiRubricMiddleware


REPO_ROOT = Path(__file__).resolve().parents[2]

logger = logging.getLogger(__name__)

# Offset in 100ns units between the Gregorian UUID epoch (1582-10-15) and Unix (1970-01-01).
_UUID_GREG_OFFSET = 0x01b21dd213814000


def _checkpoint_unix(checkpoint_id: str) -> float | None:
    """Decode a LangGraph checkpoint_id (UUID v6) to Unix seconds, or None.

    Checkpoint ids are time-ordered UUID v6, so the most-recent checkpoint per
    thread doubles as a "last activity" timestamp — used by the orphan sweep's
    ``--older-than`` filter to avoid evicting threads with recent (possibly
    in-flight) activity. Returns None if the id isn't a parseable v6.
    """
    try:
        h = checkpoint_id.replace("-", "")
        time_high = int(h[0:8], 16)
        time_mid = int(h[8:12], 16)
        time_low = int(h[12:16], 16) & 0x0FFF  # drop the version nibble
        ticks = (time_high << 28) | (time_mid << 12) | time_low
        return (ticks - _UUID_GREG_OFFSET) / 1e7
    except (ValueError, IndexError, AttributeError):
        return None

# --- module-level singletons, created once ---
_checkpoint_conn = None
_checkpointer = None


async def _get_async_checkpointer() -> AsyncSqliteSaver:
    """Lazily initialize and reuse an async SQLite checkpointer."""
    global _checkpoint_conn, _checkpointer

    if _checkpointer is None:
        _checkpoint_conn = await aiosqlite.connect(str(SESSIONS_DIR / "checkpoints.db"))
        _checkpointer = AsyncSqliteSaver(_checkpoint_conn)
        await _checkpointer.setup() # creates tables if not exist

    return _checkpointer


async def prune_checkpoints(thread_ids: list[str], *, vacuum: bool = False) -> None:
    """Evict all checkpoint state for the given threads from checkpoints.db.

    Counterpart to ``prune_sessions``: once a session row is pruned the thread
    will never be resumed, so its checkpoint is pure garbage. Keeping the two
    DBs in lockstep (same ``thread_id`` join key) stops checkpoints.db drifting
    into orphaned state.

    Uses ``adelete_thread`` (full, all-or-nothing eviction per thread) rather
    than ``aprune(strategy="keep_latest")``: keep_latest is **DeltaChannel-unsafe**
    — it can sever the parent chain so a surviving checkpoint silently
    reconstructs with empty channels (no error raised). Full deletion has no
    chain to sever. See ``src/sessions/README.md``.

    ``vacuum=True`` runs a single ``VACUUM`` after the deletes to return freed
    pages to the OS — ``DELETE`` (which is all ``adelete_thread`` does) only
    moves pages to SQLite's freelist, so the file never shrinks on its own.
    VACUUM rewrites the whole DB (brief exclusive lock); run it once, opt-in.

    Async because the checkpointer is the async ``AsyncSqliteSaver`` singleton.
    Call it from a sync context via ``asyncio.run`` (see the CLI ``prune``
    command) — never from inside ``prune_sessions``, which stays sync.
    """
    if not thread_ids:
        return

    checkpointer = await _get_async_checkpointer()
    for thread_id in thread_ids:
        await checkpointer.adelete_thread(thread_id)  # commits per thread
    if vacuum:
        # adelete_thread already committed, so no open transaction blocks VACUUM.
        await checkpointer.conn.execute("VACUUM")
        await checkpointer.conn.commit()
    logger.debug("Pruned checkpoints for %d threads (vacuum=%s)", len(thread_ids), vacuum)


async def find_orphan_checkpoint_threads(
    known_session_ids: set[str],
) -> list[tuple[str, float | None]]:
    """Return ``(thread_id, last_activity_unix)`` for checkpoint threads with no session row.

    Read-only. Orphans accumulate from runs that never wrote a session row —
    ``--no-save`` turns, eval/test threads, or history that predates sessions.db.
    The coupled ``prune_checkpoints`` (driven by deleted *session* rows) can
    never reach them, so they're swept separately.

    ``known_session_ids`` is passed in (read from sessions.db by the sync caller)
    rather than joined here, keeping this function agnostic of the sessions DB.

    Each orphan is returned with its last-activity time, decoded from
    ``MAX(checkpoint_id)`` (checkpoint ids are time-ordered UUID v6, so the max
    per thread is its most recent write); ``None`` if it can't be decoded. The
    caller applies any ``--older-than`` recency filter — returning the timestamps
    (rather than pre-filtering) lets the CLI report *why* a filter excluded things.
    """
    checkpointer = await _get_async_checkpointer()
    # MAX(checkpoint_id) is the latest checkpoint per thread (v6 sorts by time).
    async with checkpointer.conn.execute(
        "SELECT thread_id, MAX(checkpoint_id) FROM checkpoints GROUP BY thread_id"
    ) as cur:
        rows = await cur.fetchall()

    return [
        (thread_id, _checkpoint_unix(last_checkpoint_id))
        for thread_id, last_checkpoint_id in rows
        if thread_id not in known_session_ids
    ]


async def close_checkpointer() -> None:
    """Explicitly close the async checkpointer connection.

    Call this at app/notebook shutdown to flush SQLite WAL state and release
    the file handle cleanly.
    """
    global _checkpoint_conn, _checkpointer

    if _checkpoint_conn is not None:
        await _checkpoint_conn.close()
        _checkpoint_conn = None
        _checkpointer = None


async def create_supervisor(thread_id: str | None = None, eval_mode: bool = False):
    """
    Create the main supervisor agent for a conversation thread.

    Args:
        thread_id: Identifier used to locate or create the Daytona sandbox for the
            visualization subagent. Ignored in eval_mode (no sandbox created).
        eval_mode: When True, creates a leaner agent safe for automated ingest/query eval:
            - No Daytona sandbox / marp-slide-creator subagent (use eval_mode=False for marp).
            - HITL still enabled on execute / write_file / edit_file (auto-approved in
              eval/run_weekly_eval.py).
            - GuardedLocalShellBackend limits reads to wiki/skills/memories (+ config.yaml)
              and writes to wiki/; shell commands gated by HITL in run_weekly_eval.py.

    Returns:
        A DeepAgent supervisor instance produced by `create_deep_agent(...)`.
    """
    if not thread_id:
        thread_id = str(uuid7())

    subagents = []
    if not eval_mode:
        # Lazy import: pulls in the Daytona SDK (~40s) only when we actually build a sandbox.
        from src.agents.daytona_agent import create_daytona_agent

        logger.info("Creating Daytona agent for marp slide creation")
        # Create Daytona agent for marp slide creation
        _daytona_backend, daytona_sandbox, visual_agent = create_daytona_agent(
            model=set_up_llms(get_model_spec("subagent")),
            thread_id=thread_id,
            skills=[str(REPO_ROOT / "skills/marp-slide")],
        )
        register_sandbox(thread_id, daytona_sandbox.id)
        subagents = [CompiledSubAgent(
            name="marp-slide-creator",
            description="For creating Marp slides/ presentations",
            runnable=visual_agent,
            interrupt_on={"execute": True, "write_file": True, "edit_file": True},
        )]

    supervisor_backend = GuardedLocalShellBackend(
        root_dir=str(REPO_ROOT),
        virtual_mode=True,
        # Without this the shell gets an empty environment: `gh` reports itself as not
        # logged in, and `git push` over ssh cannot reach the agent. Allowlisted, so no
        # API key is ever visible to a shell command — see backend_wrapper.shell_env.
        env=shell_env(),
        eval_mode=eval_mode,
    )

    checkpointer = await _get_async_checkpointer()

    supervisor = create_deep_agent(
        model=set_up_llms(get_model_spec("supervisor")),
        skills=["/skills/"],
        memory=["memories/AGENTS.md","memories/USER.md"],
        system_prompt=PHASE_1_SUPERVISOR_PROMPT,
        backend=supervisor_backend,
        tools=all_tools,
        store=InMemoryStore(),
        checkpointer=checkpointer,
        subagents=subagents,
        interrupt_on={
            "execute": True,
            "write_file": True,
            "edit_file": True,
        },
        middleware=[
            # Redact emails in user input before sending to model
            PIIMiddleware(
                "email",
                strategy="redact",
                apply_to_input=True,
            ),
            # Mask credit cards in user input
            PIIMiddleware(
                "credit_card",
                strategy="mask",
                apply_to_input=True,
            ),
            # Block API keys - raise error if detected
            PIIMiddleware(
                "api_key",
                detector=r"sk-[a-zA-Z0-9]{32}",
                strategy="redact",
                apply_to_input=True,
            ),
            ModelCallLimitMiddleware(
                run_limit=20,        # sized for ingest worst case
                #thread_limit=100,    # generous for long query sessions
                exit_behavior="end"
            ),
            ToolCallLimitMiddleware(
                tool_name="web_extract",
                thread_limit=4, # across all runs in the thread
                run_limit=2, # across all calls in a single run
            ),
            # Loop 2: verify wiki work before the run is allowed to finish.
            # Deterministic checks only — no LLM call. Toggle via
            # `verification.enabled` in config.yaml.
            WikiRubricMiddleware.from_config(),
            # AnthropicPromptCachingMiddleware(
            #     ttl="10m",
            #     min_messages_to_cache=2, # only cache if it's multi-turn conversation
            #     unsupported_model_behavior="ignore"
            # )
        ],
    )

    return supervisor