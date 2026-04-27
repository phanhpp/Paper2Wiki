from deepagents.backends import LocalShellBackend
from deepagents.backends.protocol import ReadResult, WriteResult, EditResult

SENSITIVE_PATTERNS = [".env", "secrets.", "credentials.", "id_rsa", ".pem", ".key", ".aws/", ".ssh/"]

class GuardedLocalShellBackend(LocalShellBackend):
    """LocalShellBackend that blocks reads/writes on sensitive files."""
    
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        
    def _is_sensitive(self, path: str) -> bool:
        name = path.rsplit("/", 1)[-1]
        return any(p in path or name.startswith(p) for p in SENSITIVE_PATTERNS)
    
    def read(self, file_path, offset=0, limit=2000):
        if self._is_sensitive(file_path):
            return ReadResult(error=f"Reading {file_path} is blocked")
        return super().read(file_path, offset=offset, limit=limit)
    
    def write(self, file_path, content):
        if self._is_sensitive(file_path):
            return WriteResult(error=f"Writing {file_path} is blocked")
        return super().write(file_path, content)
    
    def edit(self, file_path, old_string, new_string, replace_all=False):
        if self._is_sensitive(file_path):
            return EditResult(error=f"Editing {file_path} is blocked")
        return super().edit(file_path, old_string, new_string, replace_all)