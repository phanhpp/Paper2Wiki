"""
Create coding/visualize agent with Daytona sandbox for safe code execution
"""
from daytona import CreateSandboxFromSnapshotParams, Daytona
from deepagents import create_deep_agent
from langchain_daytona import DaytonaSandbox
from langchain_anthropic import ChatAnthropic

def create_sandbox_subagent(model: ChatAnthropic, thread_id: str):
    client = Daytona()

    # Get or create sandbox by thread_id
    try:
        sandbox = client.find_one(labels={"thread_id": thread_id})
    except Exception:
        params = CreateSandboxFromSnapshotParams(
            labels={"thread_id": thread_id},
            # Add TTL so the sandbox is cleaned up when idle
            auto_stop_interval=5,    # stop after 5min idle (default is 15)
            auto_delete_interval=10, # delete 10min after stopped
        )
        sandbox = client.create(params)

    backend = DaytonaSandbox(sandbox=sandbox)

    agent = create_deep_agent(
        model=model,
        backend=backend,
        system_prompt="You are a coding assistant with sandbox access. You can create and run code in the sandbox.",
    )
    return agent