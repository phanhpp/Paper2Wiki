from pathlib import Path
from deepagents import create_deep_agent, FilesystemPermission, CompiledSubAgent
from deepagents.backends import CompositeBackend, StateBackend, FilesystemBackend, LocalShellBackend
from langchain.chat_models import init_chat_model
from src.tools.ingest_tools import all_tools
from src.prompts.system_prompt import PHASE_1_SUPERVISOR_PROMPT
from langchain_anthropic import ChatAnthropic
import os
from langgraph.store.memory import InMemoryStore
from langgraph.checkpoint.memory import MemorySaver
from src.agents.backend_wrapper import GuardedLocalShellBackend
from src.agents.daytona_agent import create_sandbox_subagent

# 1. Check if user set a specific path in their .env
# 2. If not, default to the folder inside the repo
# src/agents/agent.py → parents[2] = repo root
REPO_ROOT    = Path(__file__).resolve().parents[2] 
WIKI_PATH = os.getenv("WIKI_PATH", REPO_ROOT / "wiki")

print(f"REPO_ROOT: {REPO_ROOT}")
print(f"WIKI_PATH:  {WIKI_PATH}")

haiku_llm = ChatAnthropic(
    model="claude-haiku-4-5-20251001", # Fastest latency
    max_retries=8,
    timeout=120.0
    # not support adaptive thinking but does support extended thinking
    # only Opus and Sonnet 4.5+ support effort parameter
)

supervisor_llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    max_retries=8,
    timeout=120.0,
    effort="medium", # By default, Claude uses high effort, spending as many tokens as needed for excellent results
    thinking={"type": "adaptive"},
    # temperature=0.0,
    max_tokens=8000,
)

#Human-in-the-loop requires a checkpointer to persist agent state between the interrupt and resume
checkpointer = MemorySaver()

# Backend
#inner_backend = LocalShellBackend(root_dir=str(REPO_ROOT), virtual_mode=True) 
backend = GuardedLocalShellBackend(root_dir=str(REPO_ROOT), virtual_mode=True)
print(type(backend).__mro__)  # mro means method resolution order, show the inheritance hierarchy


def create_supervisor(thread_id: str):
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
    # visualization coding agent
    visual_agent = create_sandbox_subagent(model=haiku_llm, thread_id=thread_id)

    custom_subagent = CompiledSubAgent(
        name="marp-slides-creator",
        description="Specialized agent for creating marp slides",
        runnable=visual_agent
    )

    #Permissions only apply to the built-in filesystem tools (ls, read_file, glob, grep, write_file, edit_file).
    supervisor = create_deep_agent(
        model=haiku_llm,
        skills=[str(REPO_ROOT / "skills/")],
        memory=[str(REPO_ROOT / "memories/AGENTS.md")],
        system_prompt=PHASE_1_SUPERVISOR_PROMPT,
        backend=backend,
        tools=all_tools, # custom tools plus built-in: read_file, write_file, edit_file, ls, glob, grep, execute
        store=InMemoryStore(),
        checkpointer=checkpointer,  # Required!
        subagents=[custom_subagent],
        interrupt_on={
            "execute": True,
            "write_file": True,
            "edit_file": True,
            },
    )

    print(PHASE_1_SUPERVISOR_PROMPT)

    return supervisor

# # Print system prompt
# agent

# ingest_subagent = {
#     "name": "ingest",
#     "skills": ["/skills/"],
#     "permissions": [
#         FilesystemPermission(paths=["/skills/**"],    operations=["read"],         mode="allow"),
#         FilesystemPermission(paths=["/raw/", "/raw/**"],       operations=["read", "write"], mode="allow"),
#         FilesystemPermission(paths=["/wiki/","/wiki/**"],      operations=["read", "write"], mode="allow"),
#         FilesystemPermission(paths=["/memories/**"],  operations=["read"],         mode="allow"),
#         FilesystemPermission(paths=["/workspace/**"], operations=["read", "write"], mode="allow"),
#         FilesystemPermission(paths=["/**"],           operations=["read", "write"], mode="deny"),
#     ],
#     "description": "Paper ingestion, wiki maintenance, citation extract",
#     "system_prompt": INGEST_AGENT_SYSTEM_PROMPT,
#     "tools": all_tools,
# }

# backend=CompositeBackend(
#         default=StateBackend(),
#         routes={
#             "/raw/":      FilesystemBackend(root_dir=str(raw_dir),      virtual_mode=True),
#             "/wiki/":     FilesystemBackend(root_dir=str(wiki_dir),     virtual_mode=True),
#             "/skills/":   FilesystemBackend(root_dir=str(skills_dir),   virtual_mode=True),
#             "/memories/": FilesystemBackend(root_dir=str(memories_dir), virtual_mode=True),
#         },
#     )