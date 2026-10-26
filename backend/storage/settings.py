"""Storage settings, read from the environment first and then from .env.

Unlike utils.env.load_env_file, nothing is copied into os.environ: reading one storage
setting never makes the other keys in .env visible to the rest of the process.
Values are never logged or put in error messages.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy.engine import make_url

from ..utils.env import REPO_DIR, find_env_file

DATABASE_URL = "DATABASE_URL"
TEST_DATABASE_URL = "TEST_DATABASE_URL"
STORAGE_DIR = "SENIORLIFE_STORAGE_DIR"
STORAGE_KEY = "SENIORLIFE_STORAGE_KEY"
DEFAULT_STORAGE_DIR = REPO_DIR / "var" / "documents"  # git-ignored
HEX_KEY = re.compile(r"[0-9a-fA-F]{64}")


class StorageSettingError(RuntimeError):
    """A storage setting is missing or malformed. The message names the setting, never its value."""


def setting(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if value:
        return value
    env_file = find_env_file()
    if env_file is None:
        return ""
    return (dotenv_values(env_file).get(name) or "").strip()


def database_url(name: str = DATABASE_URL) -> str:
    """The SQLAlchemy async URL (postgresql+asyncpg://...)."""
    url = setting(name)
    if not url:
        raise StorageSettingError(f"{name} is not set; document storage needs a PostgreSQL database")
    try:
        parsed = make_url(url)
    except Exception:
        raise StorageSettingError(f"{name} is not a valid database URL") from None
    if parsed.get_backend_name() != "postgresql":
        raise StorageSettingError(f"{name} must point to PostgreSQL")
    return url


def with_driver(url: str, driver: str) -> str:
    """The same database with another driver: "asyncpg" for the app, "psycopg" for
    migrations, "" for a plain postgresql:// URL (the checkpointer)."""
    name = f"postgresql+{driver}" if driver else "postgresql"
    return make_url(url).set(drivername=name).render_as_string(hide_password=False)


def storage_dir() -> Path:
    return Path(setting(STORAGE_DIR) or DEFAULT_STORAGE_DIR)


def storage_key() -> bytes:
    raw = setting(STORAGE_KEY)
    if not raw:
        raise StorageSettingError(f"{STORAGE_KEY} is not set; uploaded files are stored encrypted with it")
    if not HEX_KEY.fullmatch(raw):
        raise StorageSettingError(f"{STORAGE_KEY} must be 64 hex characters (32 bytes)")
    return bytes.fromhex(raw)
