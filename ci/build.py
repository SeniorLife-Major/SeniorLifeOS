"""Stage 1: Build Docker Image.

Builds the application Docker image to ensure the Dockerfile compiles
and all dependencies install without errors.
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


def build_image(image_tag: str | None = None) -> str:
    target_tag = image_tag or get_image_tag()
    print("=" * 60)
    print(f"[CI STAGE 1: BUILD] Building Docker image: {target_tag}")
    print("=" * 60)

    cmd = ["docker", "build", "-t", target_tag, "."]
    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    print(f"\n[CI STAGE 1: SUCCESS] Docker image built successfully: {target_tag}\n")
    return target_tag


def main() -> None:
    try:
        build_image()
    except subprocess.CalledProcessError as e:
        print(f"\n[CI STAGE 1: FAILED] Docker build failed with code {e.returncode}", file=sys.stderr)
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
