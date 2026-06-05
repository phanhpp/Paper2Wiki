from pathlib import Path

from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ReadResult, WriteResult, EditResult, GrepResult, LsResult

SENSITIVE_PATTERNS = [".env", "secrets.", "credentials.", "id_rsa", ".pem", ".key", ".aws/", ".ssh/"]

# In eval mode: reads allowed only from these prefixes, writes only to wiki/.
_EVAL_READ_ALLOWED = ("wiki/", "skills/", "memories/", "large_tool_results/")
_EVAL_WRITE_ALLOWED = ("wiki/",)
# Repo-root files ingest may need (web_tools registry reads config.yaml).
_EVAL_READ_EXTRA_FILES = frozenset({"config.yaml", "config.example.yaml"})


class GuardedLocalShellBackend(LocalShellBackend):
    """LocalShellBackend that blocks reads/writes on sensitive files.

    When eval_mode=True (used by run_weekly_eval.py target functions):
      - Reads: wiki/, skills/, memories/, large_tool_results/ plus config.yaml.
        large_tool_results/ stores offloaded tool outputs that were too large inline.
      - Writes/edits: only wiki/ — prevents skill patches, memory edits, source changes.
      - execute(): not blocked here; HITL + run_weekly_eval._auto_approve gate shell commands.
    """

    def __init__(self, eval_mode: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._eval_mode = eval_mode

    def _is_sensitive(self, path: str) -> bool:
        name = path.rsplit("/", 1)[-1]
        return any(p in path or name.startswith(p) for p in SENSITIVE_PATTERNS)

    def _eval_allowed(self, path: str, allowed_prefixes: tuple) -> bool:
        stripped = path.lstrip("/")
        return any(
            stripped == p.rstrip("/") or stripped.startswith(p)
            for p in allowed_prefixes
        )

    def _remap_eval_read_path(self, file_path: str) -> str:
        """Normalize paths agents often get wrong under virtual_mode (e.g. /llm-wiki/...)."""
        stripped = file_path.lstrip("/")
        if stripped in _EVAL_READ_EXTRA_FILES:
            return file_path
        if self._eval_allowed(stripped, _EVAL_READ_ALLOWED):
            return file_path
        # Skill dirs live under skills/<name>/ — remap if that file exists.
        skills_path = Path(self.cwd) / "skills" / stripped
        if skills_path.is_file() or skills_path.is_dir():
            return f"/skills/{stripped}"
        return file_path

    def _eval_read_blocked(self, file_path: str) -> str | None:
        """Return an error message if eval_mode forbids this read, else None."""
        if not self._eval_mode:
            return None
        remapped = self._remap_eval_read_path(file_path)
        stripped = remapped.lstrip("/")
        if stripped in _EVAL_READ_EXTRA_FILES:
            return None
        if self._eval_allowed(remapped, _EVAL_READ_ALLOWED):
            return None
        return (
            f"[eval_mode] Reading {file_path} is blocked — allowed: "
            f"{', '.join(_EVAL_READ_ALLOWED)} and config.yaml"
        )

    def read(self, file_path, offset=0, limit=2000):
        if self._is_sensitive(file_path):
            return ReadResult(error=f"Reading {file_path} is blocked")
        blocked = self._eval_read_blocked(file_path)
        if blocked:
            return ReadResult(error=blocked)
        remapped = self._remap_eval_read_path(file_path) if self._eval_mode else file_path
        return super().read(remapped, offset=offset, limit=limit)

    def ls(self, path: str):
        if self._is_sensitive(path):
            return LsResult(error=f"Reading {path} is blocked", entries=[])
        blocked = self._eval_read_blocked(path)
        if blocked:
            return LsResult(error=blocked, entries=[])
        return super().ls(path)

    def write(self, file_path, content):
        if self._is_sensitive(file_path):
            return WriteResult(error=f"Writing {file_path} is blocked")
        if self._eval_mode and not self._eval_allowed(file_path, _EVAL_WRITE_ALLOWED):
            return WriteResult(error=f"[eval_mode] Writing {file_path} is blocked — only wiki/ writes are allowed in eval")
        return super().write(file_path, content)

    def edit(self, file_path, old_string, new_string, replace_all=False):
        if self._is_sensitive(file_path):
            return EditResult(error=f"Editing {file_path} is blocked")
        if self._eval_mode and not self._eval_allowed(file_path, _EVAL_WRITE_ALLOWED):
            return EditResult(error=f"[eval_mode] Editing {file_path} is blocked — only wiki/ edits are allowed in eval")
        return super().edit(file_path, old_string, new_string, replace_all=replace_all)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None):
        """In eval_mode, restrict search scope to the same trees as read()."""
        if self._eval_mode and path:
            blocked = self._eval_read_blocked(path if path.startswith("/") else f"/{path}")
            if blocked:
                return GrepResult(error=blocked)
        return super().grep(pattern, path=path, glob=glob)
