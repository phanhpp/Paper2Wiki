"""
Stream agent's results: shows live progress + LLM tokens, handles interrupts.
"""
import asyncio
from langgraph.types import Command
import ast, json
from langchain_core.utils.uuid import uuid7
from langchain_core.runnables import RunnableConfig
from src.agents.agent import create_supervisor

SESSION_AUTO_APPROVE = False

def _handle_interrupts(interrupts):
    """Build decisions list from a list of Interrupt objects."""
    global SESSION_AUTO_APPROVE
    
    interrupt_value = interrupts[0].value
    action_requests = interrupt_value["action_requests"]
    review_configs = interrupt_value["review_configs"]
    config_map = {cfg["action_name"]: cfg for cfg in review_configs}
    
    decisions = []
    for action in action_requests:
        review_config = config_map[action["name"]]
        
        if SESSION_AUTO_APPROVE:
            decisions.append({"type": "approve"})
            continue
        
        print(f"\n🔔 Tool: {action['name']}")
        print(f"   Args: {action['args']}")
        print(f"   Allowed: {review_config['allowed_decisions']}")
        choice = input("[a]pprove / [e]dit / [r]eject / [yolo=auto-approve all]: ").lower()
        
        if choice == "yolo":
            SESSION_AUTO_APPROVE = True
            decisions.append({"type": "approve"})
        elif choice == "r":
            decisions.append({"type": "reject"})
        elif choice == "e":
            new_args = input("New args (JSON or Python dict): ")
            try:
                parsed = json.loads(new_args)
            except json.JSONDecodeError:
                # ast.literal_eval: parse Python literal syntax (e.g. {'a': 1}) safely—
                # no function calls or expressions, unlike eval().
                try:
                    parsed = ast.literal_eval(new_args)
                except (ValueError, SyntaxError):
                    print("Invalid input, approving original.")
                    decisions.append({"type": "approve"})
                    continue
            decisions.append({
                "type": "edit",
                "edited_action": {"name": action["name"], "args": parsed},
            })
        else:
            decisions.append({"type": "approve"})
    
    return decisions


async def run_turn_stream_async(
    user_message: str,
    config: RunnableConfig | None = None,
    thread_id: str | None = None,
):
    """Run one streamed turn with HITL interrupt support.

    Thread id resolution precedence:
    1) explicit ``thread_id`` argument
    2) ``config["configurable"]["thread_id"]``
    3) generated UUID

    The resulting id is always written back to ``config["configurable"]`` so downstream
    components (graph state/checkpointer/tools) use a single canonical thread id.
    """
    # Extract caller-provided configurable values (if any) so we can preserve them.
    configurable = dict((config or {}).get("configurable") or {})

    # Resolve the canonical thread id with explicit arg taking priority over config.
    resolved_thread_id = thread_id or configurable.get("thread_id") or str(uuid7())

    # Keep all incoming RunnableConfig fields, but enforce our resolved thread id.
    merged_config: RunnableConfig = dict(config or {})
    merged_config["configurable"] = {
        **configurable,
        "thread_id": resolved_thread_id,
    }

    agent = create_supervisor(resolved_thread_id)
    payload = {"messages": [{"role": "user", "content": user_message}]}

    while True:
        pending_interrupts = None

        # Stream values (for interrupts) + messages (for token output)
        async for chunk in agent.astream(
            payload,
            config=merged_config,
            version="v2",
            stream_mode=["values", "messages", "updates"],
        ):
            if chunk["type"] == "values":  # Interrupts ride on values stream parts in v2
                if chunk.get("interrupts"):
                    pending_interrupts = chunk["interrupts"]
                    break  # stop streaming, handle HITL

            elif chunk["type"] == "updates":
                for node_name, node_data in chunk["data"].items():
                    if not isinstance(node_data, dict):
                        continue
                    messages = node_data.get("messages", [])

                    # Handle Overwrite sentinel (extract underlying list) or skip
                    if not isinstance(messages, list):
                        messages = getattr(messages, "value", None) or []
                        if not isinstance(messages, list):
                            continue

                    for msg in messages:
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                args_preview = str(tc["args"])[:80]
                                print(f"\n🔧 {tc['name']}({args_preview})", flush=True)

            elif chunk["type"] == "messages":
                msg, metadata = chunk["data"]

                if not msg.content:
                    continue

                if isinstance(msg.content, str):
                    print(msg.content, end="", flush=True)
                elif isinstance(msg.content, list):  # Message from AI by default is a list
                    for block in msg.content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                print(text, end="", flush=True)

        if not pending_interrupts:
            print()  # newline after streaming
            break

        # Handle interrupts and resume
        print()  # newline before HITL prompt
        decisions = _handle_interrupts(pending_interrupts)
        payload = Command(resume={"decisions": decisions})
        # Loop back, stream the resumed execution


def run_turn_stream(user_message: str, config: dict | None = None, thread_id: str | None = None):
    """Sync wrapper (kept for convenience) around the async implementation."""
    return asyncio.run(run_turn_stream_async(user_message=user_message, config=config, thread_id=thread_id))