"""
Create coding/visualize agent with Daytona sandbox for safe code execution
"""
from pathlib import Path
from dotenv import load_dotenv
from daytona import CreateSandboxFromSnapshotParams, Daytona
from deepagents import create_deep_agent
from langchain_daytona import DaytonaSandbox
from langchain_anthropic import ChatAnthropic
from langgraph.checkpoint.memory import MemorySaver

from src.prompts.system_prompt import SUBAGENT_PROMPT
from src.tools.sandbox_tools import make_download_and_save_tool, make_sandbox_state_and_fs_tools

REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(REPO_ROOT / ".env")
checkpointer = MemorySaver()

def _seed_sandbox(backend: DaytonaSandbox, skill_paths: list[Path]) -> None:
    """Upload skill files from host into sandbox absolute paths."""
    files_to_upload: list[tuple[str, bytes]] = []
    for skill_dir in skill_paths:
        for file in skill_dir.rglob("*"):
            if file.is_file():
                # host: /Users/.../skills/marp-slide/SKILL.md
                # sandbox: /home/daytona/skills/marp-slide/SKILL.md
                dest = f"/home/daytona/skills/{file.relative_to(REPO_ROOT / 'skills')}"
                files_to_upload.append((str(dest), file.read_bytes()))
    if files_to_upload:
        backend.upload_files(files_to_upload)


def download_outputs(
    backend: DaytonaSandbox,
    sandbox_paths: list[str],
    host_dir: Path = REPO_ROOT / "marp-slides",
) -> list[Path]:
    """Download files from sandbox and save them under host_dir."""
    host_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for result in backend.download_files(sandbox_paths):
        if result.content is None:
            print(f"Failed: {result.path}: {result.error}")
            continue

        out_path = host_dir / Path(result.path).name
        content = result.content if isinstance(result.content, bytes) else result.content.encode()
        out_path.write_bytes(content)
        written.append(out_path)

    return written


def create_daytona_agent(
    model: ChatAnthropic,
    skills: list[str],
    auto_stop_interval: int = 3, # stop after 3min idle (default is 15)
    auto_delete_interval: int = 5, # delete 5min after stopped (default is 10)
    is_main_agent: bool = False,
    thread_id: str | None = None, # need this to restore the sandbox from the previous session
): 
    """
    Create a Daytona agent with the given model, thread_id, skills, and auto_stop_interval.
    Uses this as standalone agent or as a subagent in a larger agent.
    if use as main agent, need to explicitly create checkpointer for the agent.

    Returns:
        backend: DaytonaSandbox instance
        agent: DeepAgent instance
    """
    client = Daytona()

    # Get or create sandbox by thread_id
    try:
        sandbox = client.find_one(labels={"thread_id": thread_id})
    except Exception:
        params = CreateSandboxFromSnapshotParams(
            labels={"thread_id": thread_id},
            # Add TTL so the sandbox is cleaned up when idle
            auto_stop_interval=auto_stop_interval,  # stop after 5min idle (default is 15)
            auto_delete_interval=auto_delete_interval,  # delete 10min after stopped
        )
        sandbox = client.create(params)

    backend = DaytonaSandbox(sandbox=sandbox)

    # Host skill dirs (for upload); agent skill discovery should point to skill source root.
    host_skill_paths = [Path(s) for s in (skills or []) if Path(s).is_dir()]
    _seed_sandbox(backend, host_skill_paths)
    sandbox_skill_paths = ["/home/daytona/skills"]

    save_output_tool = make_download_and_save_tool(backend)
    sandbox_inspection_tools = make_sandbox_state_and_fs_tools(sandbox)
    
    if is_main_agent:
        # Checkpointer is REQUIRED for human-in-the-loop
        checkpointer = MemorySaver()

        agent = create_deep_agent(
            model=model,
            backend=backend,
            system_prompt=SUBAGENT_PROMPT,
            skills=sandbox_skill_paths,
            tools=[save_output_tool, *sandbox_inspection_tools],
            interrupt_on={
                "execute": True,
                "write_file": True,
                "edit_file": True,
            },
            checkpointer=checkpointer,
        )
        return backend, sandbox, agent

    # if subagent, main agent will pass checkpointer to it
    agent = create_deep_agent(
        model=model,
        backend=backend,
        system_prompt=SUBAGENT_PROMPT,
        skills=sandbox_skill_paths,
        tools=[save_output_tool, *sandbox_inspection_tools],
    )

    return backend, sandbox, agent