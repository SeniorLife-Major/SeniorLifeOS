"""Master Pipeline Runner.

Runs all CI/CD stages in sequence:
  1. Build Docker image
  2. Determine & create Git version tag
  3. Publish image to Docker Hub
  4. Push Git tag to remote GitHub repository
  5. Deploy (placeholder)
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CI_DIR = Path(__file__).parent


def run_stage(name: str, script: str, *extra_args: str) -> None:
    cmd = [sys.executable, str(CI_DIR / script), *extra_args]
    print(f"\n>>> Running {name} ({' '.join(cmd)})")
    res = subprocess.run(cmd)
    if res.returncode != 0:
        print(f"\n[PIPELINE ABORTED] {name} failed with code {res.returncode}", file=sys.stderr)
        sys.exit(res.returncode)


def main() -> None:
    print("=" * 60)
    print("Starting SeniorLife OS CI/CD Pipeline")
    print("=" * 60)

    run_stage("Stage 1: Build Image", "build.py")
    run_stage("Stage 2: Create Git Tag", "tag.py")
    run_stage("Stage 3: Publish to Docker Hub", "publish.py")
    run_stage("Stage 4: Push Git Tag", "tag.py", "--push")
    run_stage("Stage 5: Deploy Placeholder", "deploy.py")

    print("\n[PIPELINE COMPLETE] All stages executed successfully!\n")


if __name__ == "__main__":
    main()
