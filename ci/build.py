"""Stage 1: Build Docker Image.

Builds the application Docker image to ensure the Dockerfile compiles
and all dependencies install without errors.
Supports native local builds as well as multi-architecture build validation.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

IMAGE_NAME = os.environ.get("IMAGE_NAME", "seniorlifeos")
DOCKER_USERNAME = os.environ.get("DOCKER_USERNAME") or os.environ.get("DOCKERHUB_USERNAME", "")


def get_image_tag() -> str:
    if DOCKER_USERNAME:
        return f"{DOCKER_USERNAME}/{IMAGE_NAME}:latest"
    return f"{IMAGE_NAME}:latest"


def build_image(
    image_tag: str | None = None,
    version: str | None = None,
    platforms: str | None = None,
) -> str:
    target_tag = image_tag or get_image_tag()
    app_version = (
        version
        or os.environ.get("NEW_TAG")
        or os.environ.get("APP_VERSION")
        or ""
    )
    target_platforms = platforms or os.environ.get("BUILD_PLATFORMS", "")

    print("=" * 60)
    print(f"[CI STAGE 1: BUILD] Building Docker image: {target_tag}")
    if target_platforms:
        print(f"  Target Platforms: {target_platforms}")
    if app_version:
        print(f"  Target Version (Build-Arg): {app_version}")
    print("=" * 60)

    if target_platforms:
        # If multi-arch is specified, use docker buildx
        cmd = ["docker", "buildx", "build", "--platform", target_platforms]
        if "," not in target_platforms:
            cmd.extend(["--load", "-t", target_tag])
        else:
            # Multi-arch cannot load into single local docker daemon without pushing
            cmd.extend(["-t", target_tag])
    else:
        cmd = ["docker", "build", "-t", target_tag]

    if app_version:
        cmd.extend(["--build-arg", f"APP_VERSION={app_version}"])
    cmd.append(".")

    print(f"Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)

    print(f"\n[CI STAGE 1: SUCCESS] Docker image built successfully: {target_tag}\n")
    return target_tag


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Docker Image for SeniorLife OS")
    parser.add_argument("--tag", dest="tag", default=None, help="Image tag")
    parser.add_argument("--version", dest="version", default=None, help="Application version")
    parser.add_argument("--platforms", dest="platforms", default=None, help="Target platforms e.g. linux/amd64,linux/arm64")
    args = parser.parse_args()

    try:
        build_image(image_tag=args.tag, version=args.version, platforms=args.platforms)
    except subprocess.CalledProcessError as e:
        print(f"\n[CI STAGE 1: FAILED] Docker build failed with code {e.returncode}", file=sys.stderr)
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
