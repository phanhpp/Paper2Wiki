from deepagents import create_deep_agent, CompiledSubAgent
from src.tools.ingest_tools import all_tools
from src.prompts.system_prompt import PHASE_1_SUPERVISOR_PROMPT
from langgraph.store.memory import InMemoryStore
# from langgraph.checkpoint.memory import MemorySaver
import aiosqlite
from src.agents.backend_wrapper import GuardedLocalShellBackend
from src.agents.daytona_agent import create_daytona_agent
from src.agents.sandbox_utils import register_sandbox
from src.agents.llms import haiku_llm
from langchain_core.utils.uuid import uuid7
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from src.sessions.sessions_db_setup import SESSIONS_DIR
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2] 

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


async def create_supervisor(thread_id: str | None = None):
    """
    Create the main supervisor agent for a conversation thread.

    This supervisor runs on the local guarded shell backend and is configured with:
    - project skills from `skills/`
    - long-lived memory instructions from `memories/AGENTS.md`
    - a checkpoint saver for human-in-the-loop interrupt/resume
    - a single compiled subagent (`marp-agent`) whose runnable is a Daytona-sandboxed agent

    Args:
        thread_id: Identifier used to locate or create the Daytona sandbox for the
            visualization subagent.

    Returns:
        A DeepAgent supervisor instance produced by `create_deep_agent(...)`.
    """
    # Need thread id to restore the sandbox from the previous session
    if not thread_id:
        thread_id = str(uuid7())

    # Create Daytona agent for marp slide creation
    _daytona_backend, daytona_sandbox, visual_agent = create_daytona_agent(
        model=haiku_llm,
        thread_id=thread_id,
        skills=[str(REPO_ROOT / "skills/marp-slide")], # not using virture mode so can use absolute path
    )
    register_sandbox(thread_id, daytona_sandbox.id)

    # Each subagent can have its own interrupt_on configuration that overrides the main agent’s settings
    custom_subagent = CompiledSubAgent(
        name="marp-slide-creator",
        description="For creating Marp slides/ presentations",
        runnable=visual_agent,
        interrupt_on={ 
            "execute": True,
            "write_file": True,
            "edit_file": True,
        },
    )

    # Backend
    supervisor_backend = GuardedLocalShellBackend(root_dir=str(REPO_ROOT), virtual_mode=True)
    
    checkpointer = await _get_async_checkpointer()

    supervisor = create_deep_agent(
        model=haiku_llm,
        skills=["/skills/"],
        memory=["memories/AGENTS.md"],
        system_prompt=PHASE_1_SUPERVISOR_PROMPT,
        backend=supervisor_backend,
        tools=all_tools,
        store=InMemoryStore(),
        checkpointer=checkpointer,  # Required!
        subagents=[custom_subagent],
        interrupt_on={
            "execute": True,
            "write_file": True,
            "edit_file": True,
            },
    )

    return supervisor