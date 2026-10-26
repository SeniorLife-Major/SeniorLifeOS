"""Stage 2: Run Automated Tests.

Executes all Python test suites in `backend/tests` inside the built Docker image
to verify workflow logic, document ingestion, OCR parsing, and API endpoints.
"""

from __future__ import annotations

import os
import subprocess
import sys

IMAGE_NAME = os.environ.get("IMAGE_NAME", "seniorlife-backend")
DOCKER_USERNAME = os.environ.get("DOCKER_USERNAME") or os.environ.get("DOCKERHUB_USERNAME", "")


def get_image_tag() -> str:
    if DOCKER_USERNAME:
        return f"{DOCKER_USERNAME}/{IMAGE_NAME}:latest"
    return f"{IMAGE_NAME}:latest"


def run_tests(image_tag: str | None = None) -> None:
    target_tag = image_tag or get_image_tag()
    print("=" * 60)
    print(f"[CI STAGE: TEST] Running SeniorLife OS Python Test Suite")
    print(f"  Target Container: {target_tag}")
    print("=" * 60)

    # Run all backend unit and integration tests inside the built container image
    cmd = [
        "docker", "run", "--rm",
        target_tag,
        "python", "-m", "unittest", "discover",
        "-s", "backend/tests",
        "-p", "test_*.py",
        "-v",
    ]
    print(f"Running: {' '.join(cmd)}\n")
    subprocess.run(cmd, check=True)

    print("\n[CI STAGE: SUCCESS] All automated Python tests passed successfully!\n")


def main() -> None:
    try:
        run_tests()
    except subprocess.CalledProcessError as e:
        print(f"\n[CI STAGE: FAILED] Automated tests failed with exit code {e.returncode}", file=sys.stderr)
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
