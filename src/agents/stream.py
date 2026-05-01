"""
Stream agent's results: shows live progress + LLM tokens, handles interrupts.
"""

from langgraph.types import Command
import ast, json
from src.agents.agent import agent

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


def run_turn_stream(user_message: str, config: dict):
    """Stream version: shows live progress + LLM tokens, handles interrupts."""
    
    payload = {"messages": [{"role": "user", "content": user_message}]}
    
    while True:
        pending_interrupts = None
        
        # Stream values (for interrupts) + messages (for token output)
        for chunk in agent.stream(
            payload,
            config=config,
            version="v2",
            stream_mode=["values", "messages", "updates"],
        ):
            #print(f"[DEBUG] type={chunk['type']}\n\n", flush=True)
            if chunk["type"] == "values": # Interrupts ride on values stream parts in v2
                if chunk.get("interrupts"):
                    pending_interrupts = chunk["interrupts"]
                    break  # stop streaming, handle HITL
                
                # Optional: show progress as state evolves
                # data = chunk["data"]
                # last_msg = data["messages"][-1] if data.get("messages") else None
                # if last_msg and hasattr(last_msg, "tool_calls") and last_msg.tool_calls:
                #     for tc in last_msg.tool_calls:
                #         print(f"  → calling {tc['name']}")
            elif chunk["type"] == "updates":
                for node_name, node_data in chunk["data"].items():
                    if not isinstance(node_data, dict):
                        continue
                    messages = node_data.get("messages", [])
                    
                    # Handle Overwrite sentinel (extract underlying list) or skip
                    if not isinstance(messages, list):
                        # Try to unwrap Overwrite — it usually has a .value or similar
                        messages = getattr(messages, "value", None) or []
                        if not isinstance(messages, list):
                            continue
                    
                    for msg in messages:
                        if hasattr(msg, "tool_calls") and msg.tool_calls:
                            for tc in msg.tool_calls:
                                args_preview = str(tc['args'])[:80]
                                print(f"\n🔧 {tc['name']}({args_preview})", flush=True)
            elif chunk["type"] == "messages":
                msg, metadata = chunk["data"]
                
                if not msg.content:
                    continue
                # Identify message type
                msg_class = msg.__class__.__name__
                #print(f"\nMessage class: {msg_class}\n") # turn this to logging
                
                if isinstance(msg.content, str):
                    #print(f"\nMessage is a string\n")
                    print(msg.content, end="", flush=True)
                elif isinstance(msg.content, list): # Message from AI by default is a list
                    #print("\nMessage is a list\n")
                    for block in msg.content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                print(text, end="", flush=True)
                    # No more interrupts → done
        if not pending_interrupts:
            print()  # newline after streaming
            break
        
        # Handle interrupts and resume
        print()  # newline before HITL prompt
        decisions = _handle_interrupts(pending_interrupts)
        payload = Command(resume={"decisions": decisions})
        # Loop back, stream the resumed execution

# TODO: make the code cleaner
# async version
async def run_turn_stream_async(user_message: str, config: dict):
    """Async stream version: shows live progress + LLM tokens, handles interrupts."""

    payload = {"messages": [{"role": "user", "content": user_message}]}

    while True:
        pending_interrupts = None

        # Stream values (for interrupts) + messages (for token output)
        async for chunk in agent.astream(
            payload,
            config=config,
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