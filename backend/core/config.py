"""Configuration loader and environment management for SeniorLife OS.

Standard Python / FastAPI configuration powered by Pydantic models with
environment variable resolution, .env loading, and automatic Git tag versioning.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any
import yaml
from pydantic import BaseModel, Field

from .._version import get_version

_ENV_VAR_PATTERN = re.compile(r"\$\{([A-Za-z0-9_]+)(?::([^}]*))?\}")


def _resolve_env_placeholders(data: Any) -> Any:
    """Recursively replace ${VAR:default} with environment variable or fallback value."""
    if isinstance(data, dict):
        return {key: _resolve_env_placeholders(value) for key, value in data.items()}
    if isinstance(data, list):
        return [_resolve_env_placeholders(item) for item in data]
    if isinstance(data, str):
        def _replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            default_val = match.group(2) if match.group(2) is not None else ""
            val = os.getenv(var_name)
            if val is not None and val != "":
                return val
            # Automatic Git tag version resolution when APP_VERSION is not explicitly provided
            if var_name == "APP_VERSION" and (not default_val or default_val in ("auto", "git")):
                return get_version()
            return default_val

        resolved = _ENV_VAR_PATTERN.sub(_replacer, data)
        # Type conversions for standard primitive literals
        if resolved.lower() == "true":
            return True
        if resolved.lower() == "false":
            return False
        if resolved.isdigit():
            return int(resolved)
        return resolved
    return data


class AppSection(BaseModel):
    name: str = "SeniorLife OS"
    version: str = Field(default_factory=get_version)
    environment: str = "development"
    debug: bool = True
    demo_mode: bool = True


class ServerSection(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000


class DatabaseSection(BaseModel):
    url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/seniorlife_os"
    echo: bool = False
    pool_size: int = 10
    max_overflow: int = 20


class SecuritySection(BaseModel):
    hash_key: str = ""
    token_secret: str = "change-me-in-production-secret-key-32-chars-min"


class GroqConfig(BaseModel):
    model: str = "openai/gpt-oss-20b"
    api_key: str = ""
    temperature: float = 0.0


class OpenAiConfig(BaseModel):
    model: str = "gpt-4o"
    api_key: str = ""
    temperature: float = 0.0


class LlmSection(BaseModel):
    provider: str = "groq"
    groq: GroqConfig = Field(default_factory=GroqConfig)
    openai: OpenAiConfig = Field(default_factory=OpenAiConfig)


class ActuatorSection(BaseModel):
    enabled: bool = True


class MetricsSection(BaseModel):
    enabled: bool = False
    endpoint: str = "/metrics"


class AppConfig(BaseModel):
    app: AppSection = Field(default_factory=AppSection)
    server: ServerSection = Field(default_factory=ServerSection)
    database: DatabaseSection = Field(default_factory=DatabaseSection)
    security: SecuritySection = Field(default_factory=SecuritySection)
    llm: LlmSection = Field(default_factory=LlmSection)
    actuator: ActuatorSection = Field(default_factory=ActuatorSection)
    metrics: MetricsSection = Field(default_factory=MetricsSection)


# Standard Python / FastAPI alias for settings
Settings = AppConfig

_cached_config: AppConfig | None = None


def load_config(config_path: Path | str | None = None) -> AppConfig:
    """Load and parse application configuration with env var resolution."""
    global _cached_config
    if _cached_config is not None and config_path is None:
        return _cached_config

    # Automatically load .env file into os.environ if present
    try:
        from dotenv import load_dotenv
        from ..utils.env import find_env_file

        env_file = find_env_file()
        if env_file:
            load_dotenv(dotenv_path=env_file)
    except Exception:
        pass

    if config_path is None:
        base_dir = Path(__file__).resolve().parent.parent
        config_path = base_dir / "config" / "application.yml"

    path = Path(config_path)
    if not path.exists():
        _cached_config = AppConfig()
        return _cached_config

    with open(path, "r", encoding="utf-8") as f:
        raw_data = yaml.safe_load(f) or {}

    resolved_data = _resolve_env_placeholders(raw_data)
    _cached_config = AppConfig.model_validate(resolved_data)
    return _cached_config


def get_config() -> AppConfig:
    """Dependency / accessor for application config."""
    return load_config()


def get_settings() -> AppConfig:
    """Standard FastAPI dependency accessor for settings."""
    return get_config()
