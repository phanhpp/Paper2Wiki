"""LangChain tools for Daytona sandbox: host sync, state, and filesystem listing."""
import json
from pathlib import Path

from langchain_core.tools import tool
from langchain_daytona import DaytonaSandbox
from langsmith import traceable

REPO_ROOT = Path(__file__).resolve().parents[2]


def make_download_and_save_tool(backend: DaytonaSandbox):
    """Create a tool that copies sandbox output files to host safely."""

    @tool
    @traceable(run_type="tool", name="save_marp_slides", metadata={"flow": "marp-slides"})
    def save_output(sandbox_path: str, host_relative_path: str) -> str:
        """Download a file from sandbox and save under repo root.

        Args:
            sandbox_path: Absolute path in sandbox, e.g. /home/daytona/marp-slides/deck.md
            host_relative_path: Relative destination under repo root, e.g. marp-slides/deck.md
        """
        if Path(host_relative_path).is_absolute():
            return "Failed: host_relative_path must be relative to repo root."

        destination = (REPO_ROOT / host_relative_path).resolve()
        repo_root = REPO_ROOT.resolve()
        if not str(destination).startswith(str(repo_root)):
            return "Failed: destination escapes repo root."

        allowed_roots = [(repo_root / "marp-slides").resolve(), (repo_root / "wiki").resolve()]
        if not any(str(destination).startswith(str(root)) for root in allowed_roots):
            return "Failed: destination must be under `marp-slides/` or `wiki/`."

        results = backend.download_files([sandbox_path])
        if not results:
            return "Failed: no download result returned."

        result = results[0]
        if result.content is None:
            return f"Failed: {result.error}"

        destination.parent.mkdir(parents=True, exist_ok=True)
        content = result.content if isinstance(result.content, bytes) else result.content.encode()
        destination.write_bytes(content)
        return f"Saved to {destination}"

    return save_output


def make_sandbox_state_and_fs_tools(sandbox):
    """Create tools to inspect the underlying sandbox liveness and filesystem."""

    @tool
    def get_sandbox_state() -> str:
        """Refresh sandbox metadata and return current lifecycle state."""
        try:
            sandbox.refresh_data()
            state = getattr(sandbox, "state", "unknown")
            return f"Sandbox state: {state}"
        except Exception as exc:
            return f"Failed to refresh sandbox state: {exc}"

    @tool
    def list_sandbox_files(path: str = "") -> str:
        """List files under a sandbox directory.

        Args:
            path: Absolute sandbox path. If empty, uses sandbox working directory.
        """
        try:
            sandbox.refresh_data()
            state = getattr(sandbox, "state", "unknown")
        except Exception as exc:
            return f"Failed to refresh sandbox state: {exc}"

        if state != "started":
            return f"Sandbox is not alive (state={state})."

        try:
            target_path = path.strip() if path else ""
            if not target_path:
                target_path = sandbox.get_work_dir()
            if not target_path:
                target_path = "/home/daytona"

            files = sandbox.fs.list(target_path)

            payload = {
                "state": state,
                "path": target_path,
                "entries": files if isinstance(files, list) else [str(files)],
            }
            return json.dumps(payload, default=str)
        except Exception as exc:
            return f"Failed to list sandbox path `{path or '(work_dir)'}`: {exc}"

    return [get_sandbox_state, list_sandbox_files]
