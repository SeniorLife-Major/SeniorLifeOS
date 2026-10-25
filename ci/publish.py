"""Stage 3: Publish to Container Registry (GitHub Packages / GHCR or Docker Hub).

- Authenticates with the container registry (defaults to GitHub Packages ghcr.io)
- Tags the built image with the version tag and 'latest'
- Pushes both tags to the registry
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

VERSION_FILE = Path(__file__).parent / ".version"
IMAGE_NAME = os.environ.get("IMAGE_NAME", "seniorlife-backend").lower()
REGISTRY = os.environ.get("REGISTRY", "ghcr.io").lower()

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


def publish_image(version: str) -> None:
    # GitHub Container Registry naming: ghcr.io/<owner>/<image_name> (must be lowercase)
    if "ghcr.io" in REGISTRY:
        docker_repo = f"ghcr.io/{REPO_OWNER}/{IMAGE_NAME}".lower()
    else:
        docker_repo = f"{REPO_OWNER}/{IMAGE_NAME}".lower()

    versioned_tag = f"{docker_repo}:{version}"
    latest_tag = f"{docker_repo}:latest"

    # Source local image from Stage 1
    source_image = f"{IMAGE_NAME}:latest"

    print("=" * 60)
    print(f"[CI STAGE 3: PUBLISH] Publishing to {REGISTRY}: {docker_repo}")
    print(f"  - Version Tag: {versioned_tag}")
    print(f"  - Latest Tag:  {latest_tag}")
    print("=" * 60)

    # Tag versioned and latest images
    subprocess.run(["docker", "tag", source_image, versioned_tag], check=True)
    subprocess.run(["docker", "tag", source_image, latest_tag], check=True)

    # Push to Registry
    print(f"Pushing {versioned_tag}...")
    subprocess.run(["docker", "push", versioned_tag], check=True)

    print(f"Pushing {latest_tag}...")
    subprocess.run(["docker", "push", latest_tag], check=True)

    print(f"\n[CI STAGE 3: SUCCESS] Successfully published to {REGISTRY}!\n")


def main() -> None:
    try:
        version = get_new_version()
        registry_login()
        publish_image(version)
    except Exception as e:
        print(f"\n[CI STAGE 3: FAILED] Publish error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
