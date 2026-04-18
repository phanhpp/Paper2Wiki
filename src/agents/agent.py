from pathlib import Path
from deepagents import create_deep_agent, FilesystemPermission
from deepagents.backends import CompositeBackend, StateBackend, FilesystemBackend
from langchain.chat_models import init_chat_model
from src.tools.ingest_tools import all_tools
from src.tools.lint import lint_check
from src.prompts.system_prompt import INGEST_AGENT_SYSTEM_PROMPT, PHASE_1_SUPERVISOR_PROMPT

# src/agents/agent.py → parents[2] = repo root
repo_root    = Path(__file__).resolve().parents[2]
wiki_dir     = repo_root / "wiki"
skills_dir   = repo_root / "skills"
raw_dir      = repo_root / "raw"
memories_dir = repo_root / "memories"

print(f"repo_root: {repo_root}")
print(f"wiki_dir:  {wiki_dir}")
print(f"skills_dir: {skills_dir}")

ingest_subagent = {
    "name": "ingest",
    "skills": ["/skills/"],
    "permissions": [
        FilesystemPermission(paths=["/skills/**"],    operations=["read"],         mode="allow"),
        FilesystemPermission(paths=["/raw/", "/raw/**"],       operations=["read", "write"], mode="allow"),
        FilesystemPermission(paths=["/wiki/","/wiki/**"],      operations=["read", "write"], mode="allow"),
        FilesystemPermission(paths=["/memories/**"],  operations=["read"],         mode="allow"),
        FilesystemPermission(paths=["/workspace/**"], operations=["read", "write"], mode="allow"),
        FilesystemPermission(paths=["/**"],           operations=["read", "write"], mode="deny"),
    ],
    "description": "Paper ingestion, wiki maintenance, citation extract",
    "system_prompt": INGEST_AGENT_SYSTEM_PROMPT,
    "tools": all_tools,
}

_llm = init_chat_model(
    "claude-haiku-4-5-20251001",
    max_retries=8,
    timeout=120.0,
)
agent = create_deep_agent(
    model=_llm,
    skills=["/skills/"],
    memory=["/memories/AGENTS.md"],
    system_prompt=PHASE_1_SUPERVISOR_PROMPT,
    backend=CompositeBackend(
        default=StateBackend(),
        routes={
            "/raw/":      FilesystemBackend(root_dir=str(raw_dir),      virtual_mode=True),
            "/wiki/":     FilesystemBackend(root_dir=str(wiki_dir),     virtual_mode=True),
            "/skills/":   FilesystemBackend(root_dir=str(skills_dir),   virtual_mode=True),
            "/memories/": FilesystemBackend(root_dir=str(memories_dir), virtual_mode=True),
        },
    ),
    subagents=[ingest_subagent],
    tools=[lint_check],
)