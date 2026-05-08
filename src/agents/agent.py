from pathlib import Path
from deepagents import create_deep_agent, CompiledSubAgent
from src.tools.ingest_tools import all_tools
from src.prompts.system_prompt import PHASE_1_SUPERVISOR_PROMPT
from langchain_anthropic import ChatAnthropic
import os
from langgraph.store.memory import InMemoryStore
from langgraph.checkpoint.memory import MemorySaver
from src.agents.backend_wrapper import GuardedLocalShellBackend
from src.agents.daytona_agent import create_daytona_agent
from src.agents.sandbox_utils import register_sandbox
from langchain_core.utils.uuid import uuid7

# 1. Check if user set a specific path in their .env
# 2. If not, default to the folder inside the repo root: src/agents/agent.py → parents[2] = repo root
REPO_ROOT    = Path(__file__).resolve().parents[2] 
WIKI_PATH = os.getenv("WIKI_PATH", REPO_ROOT / "wiki")
SKILLS_ROOT = REPO_ROOT / "skills"

SUPERVISOR_SKILL_SOURCES = [
    (str((SKILLS_ROOT / "llm-wiki").resolve()), "Llm-wiki Skills"),
    # (str((SKILLS_ROOT / "marp-slide").resolve()), "Marp-slide Skills"),
    (str((SKILLS_ROOT / "trace-analysis").resolve()), "Trace-analysis Skills"),
]

# not support adaptive thinking but does support extended thinking
# only Opus and Sonnet 4.5+ support effort parameter
haiku_llm = ChatAnthropic(
    model="claude-haiku-4-5-20251001", # Fastest latency
    max_retries=8,
    timeout=120.0
)

supervisor_llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    max_retries=8,
    timeout=120.0,
    effort="medium", # By default, Claude uses high effort, spending as many tokens as needed for excellent results
    thinking={"type": "adaptive"}, # temperature=0.0 is not compatible with adaptive thinking
    max_tokens=8000,
)

def create_supervisor(thread_id: str | None = None):
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
    # need thread id to restore the sandbox from the previous session
    if not thread_id:
        thread_id = str(uuid7())

    # visualization coding agent
    _daytona_backend, daytona_sandbox, visual_agent = create_daytona_agent(
        model=haiku_llm,
        thread_id=thread_id,
        skills=[str(REPO_ROOT / "skills/marp-slide")],
    )
    register_sandbox(thread_id, daytona_sandbox.id)

    custom_subagent = CompiledSubAgent(
        name="marp-slide-creator",
        description="For creating Marp slides/ presentations",
        runnable=visual_agent,
        interrupt_on={ # Each subagent can have its own interrupt_on configuration that overrides the main agent’s settings
            "execute": True,
            "write_file": True,
            "edit_file": True,
        },
    )

    #Human-in-the-loop requires a checkpointer to persist agent state between the interrupt and resume
    checkpointer = MemorySaver()

    # Backend
    supervisor_backend = GuardedLocalShellBackend(root_dir=str(REPO_ROOT), virtual_mode=True)
    #print(type(backend).__mro__)  # mro = method resolution order, show the inheritance hierarchy

    supervisor = create_deep_agent(
        model=haiku_llm,
        skills=["/skills/"],
        memory=[str(REPO_ROOT / "memories/AGENTS.md")],
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