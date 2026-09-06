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
from src.text import as_text as _as_text

#: How long a finishing turn waits for the auto-title call before giving up.
_TITLE_WAIT_SECONDS = 10.0




# Todo: resolve flow_type by tools involved
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
        thread = maybe_auto_title(conn, session_id, messages)
        # Wait briefly. The titling thread is a daemon, so a one-shot `chat` would
        # otherwise exit and kill it mid-call, leaving every such session "untitled" —
        # only the REPL, which stays alive for the next prompt, ever got a title.
        # Bounded so a slow or failing provider delays exit by seconds, not forever.
        if thread is not None:
            thread.join(timeout=_TITLE_WAIT_SECONDS)


async def run_turn_stream_async(
    user_message: str,
    agent: Runnable | None = None,
    config: RunnableConfig | None = None,
    thread_id: str | None = None,
    renderer: Renderer | None = None,
    auto_approve: bool = False,
    debug: bool = False,
    auto_title: bool = True,
    persist: bool = True,
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
        # Signal "agent is thinking" before any output — the renderer shows a transient
        # spinner that the first token/tool-call tears down. Runs each iteration so the
        # post-interrupt resume (below) gets a fresh spinner during its think gap too.
        renderer.on_turn_start()

        # Stream messages (token output) + updates (tool calls). Interrupts are NOT read
        # from here — see the state check after the loop for why.
        async for chunk in agent.astream(
            payload,
            config=merged_config,
            version="v2",
            subgraphs=True,
            stream_mode=["messages", "updates"],
        ):
            if chunk["type"] == "updates":
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

                # Tool results (ToolMessage) get a collapsed preview, not the live
                # Markdown stream — echoing a 1000-line file there smears the view.
                if getattr(msg, "type", None) == "tool":
                    renderer.on_tool_result(getattr(msg, "name", None) or "tool",
                                            _as_text(msg.content))
                    continue

                if isinstance(msg.content, str):
                    renderer.on_token(msg.content)
                elif isinstance(msg.content, list):  # Message from AI by default is a list
                    for block in msg.content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")
                            if text:
                                renderer.on_token(text)

        renderer.on_turn_end()  # newline after streaming / before any HITL prompt

        # Ask the graph whether it is interrupted, rather than watching the stream.
        #
        # This used to read ``chunk["interrupts"]`` off the ``values`` stream, which
        # prompted **twice for one tool call**: ``values`` emits the whole state after
        # each super-step, so when the loop restarts to resume, the first snapshot still
        # carries the interrupt we just resolved and it looks like a new one.
        #
        # Deduplicating on ``Interrupt.id`` would be worse than the bug: the id is
        # ``xxh3_128_hexdigest(checkpoint_ns)`` (langgraph/types.py:568) — a hash of the
        # *node position*, not the individual interrupt. Two genuine approvals at the same
        # node share an id, so skipping repeats would silently auto-approve the second.
        #
        # The persisted checkpoint has no such ambiguity: it either holds a pending
        # interrupt or it does not.
        state = await agent.aget_state(merged_config)
        pending_interrupts = getattr(state, "interrupts", None)
        if not pending_interrupts:
            break

        decisions = await renderer.handle_interrupts(pending_interrupts)
        payload = Command(resume={"decisions": decisions})
        # Loop back, stream the resumed execution

    # Ephemeral mode: skip sessions.db entirely (no row, no history, no auto-title).
    # The LangGraph checkpointer still ran, so in-run HITL resume was unaffected.
    if not persist:
        renderer.on_debug("[debug] persist=False — skipping sessions.db save")
        return

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
    persist: bool = True,
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
            persist=persist,
        )
    )