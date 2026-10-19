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
import uvicorn
from uvicorn.logging import ColourizedFormatter
from backend.core.config import get_config

_log_handler = logging.StreamHandler(sys.stdout)
_log_handler.setFormatter(ColourizedFormatter(fmt="%(levelprefix)s %(message)s", use_colors=True))
logging.root.handlers = [_log_handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger(__name__)


def main():
    config = get_config()
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
