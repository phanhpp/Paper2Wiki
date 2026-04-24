# src/agents/secret_guard.py
from deepagents.backends.protocol import (
    ReadResult, WriteResult, EditResult,
)

SENSITIVE_PATTERNS = [
    ".env",
    "secrets.",
    "credentials.",
    "id_rsa",
    "id_ed25519",
    ".pem",
    ".key",
    ".aws/",
    ".ssh/",
]

# Can't inherit from BackendProtocol, the official guide is wrong
# if inherit will raise error for all the methods not explicitly overridden
class SecretGuardWrapper:
    """Blocks reads/writes/edits on sensitive files.
    
    Forwards all other method calls to the inner backend via __getattr__.
    This keeps extension methods like download_files, upload_files, execute 
    working correctly without needing to manually proxy each one.
    """

    def __init__(self, inner):
        self.inner = inner

    def __getattr__(self, name):
        # Called only when attribute isn't found on this instance/class.
        # Forwards everything we didn't explicitly override.
        return getattr(self.inner, name)

    def _is_sensitive(self, path: str) -> bool:
        name = path.rsplit("/", 1)[-1]
        return any(p in path or name.startswith(p) for p in SENSITIVE_PATTERNS)

    # --- Guarded methods ---
    def read(self, file_path: str, offset: int = 0, limit: int = 2000):
        if self._is_sensitive(file_path):
            print(f"Reading {file_path} is blocked (sensitive file)")
            return ReadResult(error=f"Reading {file_path} is blocked (sensitive file)")
        return self.inner.read(file_path, offset=offset, limit=limit)

    def write(self, file_path: str, content: str):
        if self._is_sensitive(file_path):
            return WriteResult(error=f"Writing {file_path} is blocked (sensitive file)")
        return self.inner.write(file_path, content)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False):
        if self._is_sensitive(file_path):
            return EditResult(error=f"Editing {file_path} is blocked (sensitive file)")
        return self.inner.edit(file_path, old_string, new_string, replace_all)