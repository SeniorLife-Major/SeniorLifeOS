"""Environment file loader powered by python-dotenv.

Values already set in the real environment always win, so shell variables can
override the file. Only key names are ever returned; values are never logged.
"""

from __future__ import annotations

import os
from pathlib import Path
from dotenv import dotenv_values, find_dotenv, load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = BACKEND_DIR.parent


def find_env_file() -> Path | None:
    """Prefer backend/.env, then repository root .env."""
    for candidate in (BACKEND_DIR / ".env", REPO_DIR / ".env"):
        if candidate.is_file():
            return candidate
    return None


def load_env_file(path: Path | None) -> list[str]:
    """Load variables using python-dotenv. Values already in os.environ are preserved."""
    if path is None or not Path(path).is_file():
        return []
    values = dotenv_values(dotenv_path=path)
    loaded = []
    for key, value in values.items():
        if key and key not in os.environ and value is not None:
            os.environ[key] = value
            loaded.append(key)
    if any(key.startswith(("LANGSMITH_", "LANGCHAIN_")) for key in loaded):
        # The file may have just switched LangSmith tracing on: make sure only
        # masked data can be sent (see utils/tracing.py).
        from .tracing import install_safe_client

        install_safe_client()
    return loaded
