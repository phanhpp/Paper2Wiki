import logging
from deepagents import create_deep_agent, CompiledSubAgent
from src.tools import all_tools
from src.prompts.system_prompt import PHASE_1_SUPERVISOR_PROMPT
from langgraph.store.memory import InMemoryStore
import aiosqlite
from src.agents.backend_wrapper import GuardedLocalShellBackend
# NOTE: create_daytona_agent (→ langchain_daytona → the Daytona SDK) is imported lazily inside
# create_supervisor, only when eval_mode is False. The import itself is ~3.6s; the real cost is at
# call time — create_daytona_agent provisions/restores a sandbox over the network (tens of seconds).
# Deferring keeps `import src.agents.agent` cheap and lets eval-mode runs skip Daytona entirely.
from src.agents.sandbox_utils import register_sandbox
from src.agents.llms import set_up_llms
from langchain_core.utils.uuid import uuid7
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from src.sessions.sessions_db_setup import SESSIONS_DIR
from pathlib import Path
from langchain.agents.middleware import PIIMiddleware, ModelCallLimitMiddleware, ToolCallLimitMiddleware


REPO_ROOT = Path(__file__).resolve().parents[2]

logger = logging.getLogger(__name__)

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


async def prune_checkpoints(thread_ids: list[str]) -> None:
    """Evict all checkpoint state for the given threads from checkpoints.db.

    Counterpart to ``prune_sessions``: once a session row is pruned the thread
    will never be resumed, so its checkpoint is pure garbage. Keeping the two
    DBs in lockstep (same ``thread_id`` join key) stops checkpoints.db drifting
    into orphaned state.

    Uses ``adelete_thread`` (full, all-or-nothing eviction per thread) rather
    than ``aprune(strategy="keep_latest")``: keep_latest is **DeltaChannel-unsafe**
    — it can sever the parent chain so a surviving checkpoint silently
    reconstructs with empty channels (no error raised). Full deletion has no
    chain to sever. See ``docs/prune.md`` and ``src/sessions/README.md``.

    Async because the checkpointer is the async ``AsyncSqliteSaver`` singleton.
    Call it from a sync context via ``asyncio.run`` (see the CLI ``prune``
    command) — never from inside ``prune_sessions``, which stays sync.
    """
    if not thread_ids:
        return

    checkpointer = await _get_async_checkpointer()
    for thread_id in thread_ids:
        await checkpointer.adelete_thread(thread_id)
    logger.debug("Pruned checkpoints for %d threads", len(thread_ids))


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
            model=set_up_llms("claude-haiku-4-5-20251001"),
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
        eval_mode=eval_mode,
    )

    checkpointer = await _get_async_checkpointer()

    supervisor = create_deep_agent(
        model=set_up_llms("claude-sonnet-4-6"), #claude-haiku-4-5-20251001
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
            # AnthropicPromptCachingMiddleware(
            #     ttl="10m",
            #     min_messages_to_cache=2, # only cache if it's multi-turn conversation
            #     unsupported_model_behavior="ignore"
            # )
        ],
    )

    return supervisor