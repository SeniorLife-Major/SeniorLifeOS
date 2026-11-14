"""Stage 4: Multi-Arch Build & Publish to Container Registry (GHCR / Docker Hub).

- Authenticates with the container registry (ghcr.io)
- Builds multi-architecture Docker image (linux/amd64 and linux/arm64) using Docker Buildx
- Pushes both versioned tag and 'latest' tag multi-platform manifests to the registry
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

VERSION_FILE = Path(__file__).parent / ".version"
IMAGE_NAME = os.environ.get("IMAGE_NAME", "seniorlifeos").lower()
REGISTRY = os.environ.get("REGISTRY", "ghcr.io").lower()
PLATFORMS = os.environ.get("DOCKER_PLATFORMS", "linux/amd64,linux/arm64")

# Authentication: Works automatically with GITHUB_TOKEN on GitHub Actions
REGISTRY_USER = (
    os.environ.get("REGISTRY_USER")
    or os.environ.get("GITHUB_ACTOR")
    or os.environ.get("DOCKER_USERNAME")
    or ""
)
REGISTRY_PASSWORD = (
    os.environ.get("REGISTRY_PASSWORD")
    or os.environ.get("GITHUB_TOKEN")
    or os.environ.get("DOCKER_PASSWORD")
    or ""
)
REPO_OWNER = (
    os.environ.get("REPO_OWNER")
    or os.environ.get("GITHUB_REPOSITORY_OWNER")
    or REGISTRY_USER
).lower()


def get_new_version() -> str:
    if "NEW_TAG" in os.environ:
        return os.environ["NEW_TAG"].strip()
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    raise RuntimeError("Version tag not found. Please run ci/tag.py first.")


def registry_login() -> None:
    if not REGISTRY_USER or not REGISTRY_PASSWORD:
        raise ValueError(
            f"Registry credentials missing for {REGISTRY}.\n"
            "In GitHub Actions, GITHUB_TOKEN is used automatically.\n"
            "For local testing, export REGISTRY_USER and REGISTRY_PASSWORD (or GITHUB_TOKEN)."
        )

    print(f"Logging into {REGISTRY} as '{REGISTRY_USER}'...")
    login_cmd = ["docker", "login", REGISTRY, "-u", REGISTRY_USER, "--password-stdin"]
    subprocess.run(
        login_cmd,
        input=REGISTRY_PASSWORD,
        text=True,
        check=True,
        capture_output=True,
    )
    print(f"Login to {REGISTRY} succeeded.\n")


def publish_image(version: str, platforms: str = PLATFORMS) -> None:
    # GitHub Container Registry naming: ghcr.io/<owner>/<image_name> (must be lowercase)
    if "ghcr.io" in REGISTRY:
        docker_repo = f"ghcr.io/{REPO_OWNER}/{IMAGE_NAME}".lower()
    else:
        docker_repo = f"{REPO_OWNER}/{IMAGE_NAME}".lower()

    versioned_tag = f"{docker_repo}:{version}"
    latest_tag = f"{docker_repo}:latest"

    print("=" * 60)
    print(f"[CI STAGE 4: PUBLISH] Multi-Arch Build & Publish to {REGISTRY}: {docker_repo}")
    print(f"  - Target Platforms: {platforms}")
    print(f"  - Version Tag:      {versioned_tag}")
    print(f"  - Latest Tag:       {latest_tag}")
    print("=" * 60)

    # Use docker buildx to build multi-arch (linux/amd64 and linux/arm64) and push manifest in one step
    cmd = [
        "docker", "buildx", "build",
        "--platform", platforms,
        "--build-arg", f"APP_VERSION={version}",
        "-t", versioned_tag,
        "-t", latest_tag,
        "--push",
        ".",
    ]
    print(f"Running: {' '.join(cmd)}\n")
    try:
        subprocess.run(cmd, check=True)
        print(f"\n[CI STAGE 4: SUCCESS] Successfully built and published multi-arch image ({platforms}) to {REGISTRY}!\n")
    except subprocess.CalledProcessError as e:
        print(f"\n[CI STAGE 4: FAILED] Multi-arch docker buildx failed with code {e.returncode}", file=sys.stderr)
        raise


def main() -> None:
    try:
        version = get_new_version()
        registry_login()
        publish_image(version)
    except Exception as e:
        print(f"\n[CI STAGE 4: FAILED] Publish error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
