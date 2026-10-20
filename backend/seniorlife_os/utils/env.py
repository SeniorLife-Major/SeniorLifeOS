"""Load KEY=VALUE lines from a .env file into os.environ (standard library only).

Values already set in the real environment always win, so a shell variable can
override the file. Only key names are ever returned; values are never logged.
"""

from __future__ import annotations

import os
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_DIR = BACKEND_DIR.parent


def find_env_file() -> Path | None:
    """Prefer backend/.env, then the repository root .env."""
    for candidate in (BACKEND_DIR / ".env", REPO_DIR / ".env"):
        if candidate.is_file():
            return candidate
    return None


def load_env_file(path: Path | None) -> list[str]:
    """Set variables from the file that are not already set. Returns the key names loaded."""
    if path is None:
        return []
    loaded = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    if any(key.startswith(("LANGSMITH_", "LANGCHAIN_")) for key in loaded):
        # The file may have just switched LangSmith tracing on: make sure only
        # masked data can be sent (see utils/tracing.py).
        from .tracing import install_safe_client

        install_safe_client()
    return loaded
