"""Cross-platform, OS-agnostic server launcher for SeniorLife OS backend.

Runs seamlessly on Windows (PowerShell/CMD), Linux, macOS, and Docker.
"""

from __future__ import annotations

import sys
from pathlib import Path

# 1. Enforce UTF-8 encoding across stdout/stderr on all operating systems
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 2. OS-agnostic Path Resolution (POSIX / Windows compatible via pathlib)
current_file = Path(__file__).resolve()
backend_dir = current_file.parent
project_root = backend_dir.parent

# Ensure project root is at the head of sys.path on all platforms
root_str = str(project_root)
if root_str not in sys.path:
    sys.path.insert(0, root_str)

import logging
import os
import subprocess
import uvicorn
from uvicorn.logging import ColourizedFormatter
from backend.core.config import get_config

_log_handler = logging.StreamHandler(sys.stdout)
_log_handler.setFormatter(ColourizedFormatter(fmt="%(levelprefix)s %(message)s", use_colors=True))
logging.root.handlers = [_log_handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def apply_database_migrations(config) -> None:
    """Automatically run Alembic migrations on startup if DATABASE_URL is configured."""
    db_url = os.environ.get("DATABASE_URL", "").strip() or getattr(config.database, "url", "").strip()
    if not db_url:
        logger.info("DATABASE_URL is not configured; skipping automatic database migrations.")
        return

    ini_path = backend_dir / "alembic.ini"
    if not ini_path.exists():
        logger.warning("Alembic configuration not found at %s; skipping migrations.", ini_path)
        return

    logger.info("Applying database schema migrations via Alembic...")
    try:
        res = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", str(ini_path), "upgrade", "head"],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            check=True,
        )
        if res.stdout:
            for line in res.stdout.strip().splitlines():
                logger.info("[alembic] %s", line)
        logger.info("Database schema migrations applied successfully.")
    except subprocess.CalledProcessError as err:
        logger.error("Database migration failed (exit code %s): %s", err.returncode, err.stderr or err.stdout)
    except Exception as exc:
        logger.error("Could not run database migrations: %s", exc)


def main():
    config = get_config()
    
    # 1. Automatically run database migrations if configured
    apply_database_migrations(config)

    # 2. Start Uvicorn Web Server
    logger.info("Starting SeniorLife OS Server on http://%s:%s (env: %s)...", config.server.host, config.server.port, config.app.environment)
    logger.info("Swagger Documentation: http://localhost:%s/docs", config.server.port)
    
    # app_dir ensures Uvicorn's reload subprocess resolves 'backend.main:app' on any OS
    uvicorn.run(
        "backend.main:app",
        host=config.server.host,
        port=config.server.port,
        reload=config.app.debug,
        app_dir=root_str,
    )


if __name__ == "__main__":
    main()
