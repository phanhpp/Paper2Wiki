"""
Stream agent's results: shows live progress + LLM tokens, handles interrupts.

Output goes through a ``Renderer`` (see ``src/agents/renderer.py``) so the same loop drives
notebooks (``DefaultRenderer``) and the CLI (``RichRenderer``).
"""
import asyncio
import time
from langgraph.types import Command
from langchain_core.utils.uuid import uuid7
from langchain_core.runnables import Runnable, RunnableConfig
from src.agents.agent import create_supervisor
from src.agents.renderer import Renderer, DefaultRenderer
from src.sessions.sessions_db_setup import get_sessions_conn
from src.sessions.session_manager import save_session
from src.sessions.title_manager import maybe_auto_title


def _save_session(conn, thread_id, messages, started_at, flow_type="ingest", auto_title=True):
    """Save session to db and (optionally) auto-title.

    Set ``auto_title=False`` when a manual title is already set or pending for this thread —
    auto-titling makes an LLM call whose result would just be overwritten, so skipping it
    saves tokens and latency.
    """
    session_id = save_session(
        conn=conn,
        thread_id=thread_id,
        messages=messages,
        started_at=started_at,
        flow_type=flow_type,
    )
    if auto_title:
        maybe_auto_title(conn, session_id, messages)


async def run_turn_stream_async(
    user_message: str,
    agent: Runnable | None = None,
    config: RunnableConfig | None = None,
    thread_id: str | None = None,
    renderer: Renderer | None = None,
    auto_approve: bool = False,
    debug: bool = False,
    auto_title: bool = True,
):
    """Run one streamed turn with HITL interrupt support.

    Thread id resolution precedence:
    1) explicit ``thread_id`` argument
    2) ``config["configurable"]["thread_id"]``
    3) generated UUID

    The resulting id is always written back to ``config["configurable"]`` so downstream
    components (graph state/checkpointer/tools) use a single canonical thread id.

    If you pass a custom ``agent``, use the same ``thread_id`` (or omit both so defaults
    match) wherever that agent was constructed with thread-scoped resources.

    Output is driven through ``renderer``; if none is given a ``DefaultRenderer`` is built
    from ``auto_approve``/``debug`` (when a renderer is passed those two args are ignored —
    configure them on the renderer instead).
    """
    if renderer is None:
        renderer = DefaultRenderer(auto_approve=auto_approve, debug=debug)

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

    if agent is None:
        agent = await create_supervisor(resolved_thread_id)
    
    payload = {"messages": [{"role": "user", "content": user_message}]}
    started_at = int(time.time())

    while True:
        pending_interrupts = None

        # Stream values (for interrupts) + messages (for token output)
        async for chunk in agent.astream(
            payload,
            config=merged_config,
            version="v2",
            subgraphs=True,
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
                                renderer.on_tool_call(tc["name"], tc["args"])

            elif chunk["type"] == "messages":
                msg, metadata = chunk["data"]

                if not msg.content:
                    continue

                if isinstance(msg.content, str):
                    renderer.on_token(msg.content)
                elif isinstance(msg.content, list):  # Message from AI by default is a list
                    for block in msg.content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                renderer.on_token(text)

        if not pending_interrupts:
            renderer.on_turn_end()  # newline after streaming
            break

        # Handle interrupts and resume
        renderer.on_turn_end()  # newline before HITL prompt
        decisions = renderer.handle_interrupts(pending_interrupts)
        payload = Command(resume={"decisions": decisions})
        # Loop back, stream the resumed execution

    # save session to db
    final_state = await agent.aget_state(merged_config)
    messages = final_state.values["messages"]
    debug_content_types = [type(getattr(m, "content", None)).__name__ for m in messages]
    renderer.on_debug(f"[debug] session message content types: {debug_content_types}")
    _save_session(get_sessions_conn(), resolved_thread_id, messages, started_at, auto_title=auto_title)
    

def run_turn_stream(
    user_message: str,
    agent: Runnable | None = None,
    config: dict | None = None,
    thread_id: str | None = None,
    renderer: Renderer | None = None,
    auto_approve: bool = False,
    debug: bool = False,
    auto_title: bool = True,
):
    """Sync wrapper (kept for convenience) around the async implementation."""
    return asyncio.run(
        run_turn_stream_async(
            user_message=user_message,
            agent=agent,
            config=config,
            thread_id=thread_id,
            renderer=renderer,
            auto_approve=auto_approve,
            debug=debug,
            auto_title=auto_title,
        )
    )