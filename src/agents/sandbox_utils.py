"""Utilities for tracking Daytona sandboxes by thread id."""

from daytona import Daytona

_client: Daytona | None = None
_sandbox_ids: dict[str, str] = {}


def _get_client() -> Daytona:
    global _client
    if _client is None:
        _client = Daytona()
    return _client


def register_sandbox(thread_id: str, sandbox_id: str) -> None:
    """Store sandbox id for a thread."""
    _sandbox_ids[thread_id] = sandbox_id


def get_sandbox_id(thread_id: str) -> str:
    """Get sandbox id for a thread id or raise a clear error."""
    sandbox_id = _sandbox_ids.get(thread_id)
    if sandbox_id is None:
        raise KeyError(f"No sandbox registered for thread_id={thread_id!r}")
    return sandbox_id


def get_sandbox(thread_id: str):
    """Reconstruct sandbox object from Daytona API."""
    sandbox_id = get_sandbox_id(thread_id)
    return _get_client().get(sandbox_id)


def clear_sandbox(thread_id: str) -> None:
    """Remove sandbox id reference for completed threads."""
    _sandbox_ids.pop(thread_id, None)


def inspect_sandbox(
    thread_id: str, 
    path: str | None = None,
) -> dict[str, object]:
    """Return compact sandbox debug info. Safe to call on stopped sandboxes."""
    try:
        sandbox = get_sandbox(thread_id)
    except Exception as exc:
        return {"error": str(exc)}

    sandbox.refresh_data()

    state = getattr(sandbox, "state", "unknown")
    work_dir = sandbox.get_work_dir() if state == "started" else None
    target_path = path or work_dir or "/home/daytona"

    entries: list[object] = []
    if state == "started":
        listed = sandbox.fs.list_files(target_path)
        entries = listed if isinstance(listed, list) else [listed]

    return {
        "sandbox_id": getattr(sandbox, "id", None),
        "state": state,
        "work_dir": work_dir,
        "path": target_path,
        "entries": entries,
    }
