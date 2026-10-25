"""Single source of truth for SeniorLife OS application version.

Standard Python version management:
1. Environment variable 'APP_VERSION' (Docker / CI production builds)
2. Dynamic git tag via 'git describe --tags --abbrev=0' (local development)
3. Default fallback version ('1.0.0')
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def get_version() -> str:
    # 1. Environment variable (Docker containers, CI/CD, production)
    env_version = os.environ.get("APP_VERSION", "").strip()
    if env_version:
        return env_version.lstrip("v")

    # 2. Local git repository (development)
    try:
        repo_dir = Path(__file__).resolve().parent.parent
        res = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip().lstrip("v")
    except Exception:
        pass

    # 3. Fallback
    return "1.0.0"


__version__ = get_version()
