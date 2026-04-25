from pathlib import Path
from deepagents import create_deep_agent, FilesystemPermission
from deepagents.backends import CompositeBackend, StateBackend, FilesystemBackend, LocalShellBackend
from langchain.chat_models import init_chat_model
from src.tools.ingest_tools import all_tools
from src.tools.lint import lint_check
from src.prompts.system_prompt import INGEST_AGENT_SYSTEM_PROMPT, PHASE_1_SUPERVISOR_PROMPT
from langchain_anthropic import ChatAnthropic
import os
from langgraph.store.memory import InMemoryStore
from langgraph.checkpoint.memory import MemorySaver
from src.agents.backend_wrapper import GuardedLocalShellBackend
#Human-in-the-loop requires a checkpointer to persist agent state between the interrupt and resume
checkpointer = MemorySaver()
# 1. Check if user set a specific path in their .env
# 2. If not, default to the folder inside the repo
# src/agents/agent.py → parents[2] = repo root
REPO_ROOT    = Path(__file__).resolve().parents[2] 
WIKI_PATH = os.getenv("WIKI_PATH", REPO_ROOT / "wiki")

print(f"REPO_ROOT: {REPO_ROOT}")
print(f"WIKI_PATH:  {WIKI_PATH}")

supervisor_llm = ChatAnthropic(
    model="claude-sonnet-4-6",
    max_retries=8,
    timeout=120.0,
    effort="medium", # By default, Claude uses high effort, spending as many tokens as needed for excellent results
    thinking={"type": "adaptive"},
    # temperature=0.0,
    max_tokens=8000,
)

inner_backend = LocalShellBackend(root_dir=str(REPO_ROOT))
backend = GuardedLocalShellBackend(root_dir=str(REPO_ROOT))
print(type(backend).__mro__)  # mro means method resolution order, show the inheritance hierarchy


agent = create_deep_agent(
    model=supervisor_llm,
    skills=["/skills/"],
    memory=["/memories/AGENTS.md"],
    system_prompt=PHASE_1_SUPERVISOR_PROMPT,
    backend=backend,
    # subagents=[ingest_subagent],
    tools=all_tools, # custom tools plus built-in: read_file, write_file, edit_file, ls, glob, grep, execute
    store=InMemoryStore(),
    checkpointer=checkpointer,  # Required!
    interrupt_on={
        "execute": {"allowed_decisions": ["approve", "edit", "reject"]},
        # Also gate destructive filesystem ops
        "write_file": True,
        "edit_file": True,
        "ls": True, 
        "read_file": {"allowed_decisions": ["approve", "reject"]},
    },
)



# # TODO: put the args in config file
# subagent_llm = ChatAnthropic(
#     model="claude-haiku-4-5-20251001", # Fastest latency
#     max_retries=8,
#     timeout=120.0
#     # not support adaptive thinking but does support extended thinking
#     # only Opus and Sonnet 4.5+ support effort parameter
# )

print(PHASE_1_SUPERVISOR_PROMPT)

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