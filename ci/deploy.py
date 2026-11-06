"""Stage 6: GitOps Deployment Manifest Updater for Kubernetes & ArgoCD.

- Reads current/new release version tag.
- Updates container image tags in Kubernetes manifests:
    - k8s/backend-deployment.yaml (initContainer migration & backend container)
    - k8s/kustomization.yaml (images[].newTag)
- Commits changes with message format:
    deployed:<version> [skip ci]
- Pushes commit to GitHub repository so ArgoCD automatically detects and reconciles.
- Skips CI re-triggers using [skip ci] and path-ignore filters.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
K8S_DIR = REPO_ROOT / "k8s"
DEPLOYMENT_FILE = K8S_DIR / "backend-deployment.yaml"
KUSTOMIZATION_FILE = K8S_DIR / "kustomization.yaml"
VERSION_FILE = Path(__file__).parent / ".version"

REPO_OWNER = os.environ.get("REPO_OWNER") or os.environ.get("GITHUB_REPOSITORY_OWNER") or "seniorlife-major"
IMAGE_NAME = os.environ.get("IMAGE_NAME", "seniorlifeos")
REGISTRY = os.environ.get("REGISTRY", "ghcr.io")


def get_version(explicit_version: str | None = None) -> str:
    """Retrieve version from CLI argument, environment, or file."""
    if explicit_version and explicit_version.strip():
        return explicit_version.strip()
    if os.environ.get("NEW_TAG", "").strip():
        return os.environ["NEW_TAG"].strip()
    if VERSION_FILE.exists():
        tag = VERSION_FILE.read_text(encoding="utf-8").strip()
        if tag:
            return tag
    try:
        res = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            capture_output=True,
            text=True,
            check=True,
        )
        if res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass
    return "v1.0.0"


def update_backend_deployment(version: str) -> bool:
    """Update container and initContainer image tags in k8s/backend-deployment.yaml."""
    if not DEPLOYMENT_FILE.exists():
        print(f"Error: {DEPLOYMENT_FILE} not found.", file=sys.stderr)
        return False

    content = DEPLOYMENT_FILE.read_text(encoding="utf-8")
    full_image_prefix = f"{REGISTRY}/{REPO_OWNER.lower()}/{IMAGE_NAME.lower()}"

    # Match image declarations matching either the specific full image or generic seniorlifeos/backend
    pattern = re.compile(
        r"(image:\s*)([^\s]+(?:seniorlifeos|seniorlife-backend)):([^\s]+)",
        re.MULTILINE,
    )

    new_content, count = pattern.subn(r"\g<1>" + full_image_prefix + f":{version}", content)

    if count == 0:
        # Fallback regex for any backend image pattern in the file
        fallback_pattern = re.compile(
            r"(image:\s*ghcr\.io/[^:]+):([^\s]+)",
            re.MULTILINE,
        )
        new_content, count = fallback_pattern.subn(r"\g<1>:" + version, content)

    if content != new_content:
        DEPLOYMENT_FILE.write_text(new_content, encoding="utf-8")
        print(f"  [✓] Updated {DEPLOYMENT_FILE.name}: replaced {count} image tag(s) with '{version}'")
        return True
    else:
        print(f"  [-] {DEPLOYMENT_FILE.name} already set to version '{version}'")
        return False


def update_kustomization(version: str) -> bool:
    """Update newTag in k8s/kustomization.yaml."""
    if not KUSTOMIZATION_FILE.exists():
        print(f"Warning: {KUSTOMIZATION_FILE} not found.", file=sys.stderr)
        return False

    content = KUSTOMIZATION_FILE.read_text(encoding="utf-8")
    pattern = re.compile(r"(newTag:\s*)([^\s]+)", re.MULTILINE)
    new_content, count = pattern.subn(rf"\g<1>{version}", content)

    if content != new_content:
        KUSTOMIZATION_FILE.write_text(new_content, encoding="utf-8")
        print(f"  [✓] Updated {KUSTOMIZATION_FILE.name}: set newTag to '{version}'")
        return True
    else:
        print(f"  [-] {KUSTOMIZATION_FILE.name} already set to '{version}'")
        return False


def configure_git_user() -> None:
    """Ensure git user name and email are configured for committing."""
    res = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True)
    if not res.stdout.strip():
        subprocess.run(["git", "config", "--global", "user.name", "github-actions[bot]"], check=False)
        subprocess.run(
            ["git", "config", "--global", "user.email", "github-actions[bot]@users.noreply.github.com"],
            check=False,
        )


def commit_and_push(version: str) -> bool:
    """Commit updated k8s manifests and push with commit message 'deployed:<version> [skip ci]'."""
    configure_git_user()

    # Stage k8s directory changes
    subprocess.run(["git", "add", "k8s/"], check=True)

    # Check for staged changes
    status = subprocess.run(["git", "diff", "--staged", "--name-only"], capture_output=True, text=True, check=True)
    staged_files = [f.strip() for f in status.stdout.splitlines() if f.strip()]

    if not staged_files:
        print("  [i] No manifest changes detected to commit.")
        return True

    print(f"  [+] Staged files: {', '.join(staged_files)}")

    # Commit message with 'deployed:<version>' and '[skip ci]'
    commit_msg_title = f"deployed:{version} [skip ci]"
    commit_msg_body = f"Automated GitOps deployment manifest update for version {version}\n\n[skip ci]"

    commit_cmd = ["git", "commit", "-m", commit_msg_title, "-m", commit_msg_body]
    subprocess.run(commit_cmd, check=True)
    print(f"  [✓] Committed changes: '{commit_msg_title}'")

    # Fetch and push to remote
    print("  [+] Pushing GitOps manifests to remote origin...")
    try:
        subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=False)
        subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)
        print(f"  [✓] Successfully pushed {commit_msg_title} to origin/main!")
        return True
    except subprocess.CalledProcessError as e:
        print(f"  [!] Failed to push to remote: {e}", file=sys.stderr)
        return False


def deploy(version: str | None = None, push: bool = False) -> None:
    resolved_version = get_version(version)

    print("=" * 60)
    print("[CI STAGE 6: GITOPS DEPLOY] Updating ArgoCD Kubernetes Manifests")
    print(f"  Target Version: {resolved_version}")
    print(f"  Target Image:   {REGISTRY}/{REPO_OWNER.lower()}/{IMAGE_NAME.lower()}:{resolved_version}")
    print(f"  Push to Git:    {push}")
    print("=" * 60)

    updated_dep = update_backend_deployment(resolved_version)
    updated_kust = update_kustomization(resolved_version)

    if push:
        success = commit_and_push(resolved_version)
        if not success:
            sys.exit(1)
    else:
        print("\n  [Note] Push skipped (use --push to commit and push changes to git remote).")

    print("\n" + "=" * 60)
    print(f"[CI STAGE 6: SUCCESS] ArgoCD manifests updated for {resolved_version}")
    print("=" * 60 + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Update Kubernetes manifests for ArgoCD GitOps deployment.")
    parser.add_argument("version", nargs="?", default=None, help="Version tag to deploy (e.g. v1.1.1)")
    parser.add_argument("--push", action="store_true", help="Commit and push changes to git repository")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    should_push = args.push or os.environ.get("DEPLOY_PUSH", "").lower() in ("1", "true")
    deploy(version=args.version, push=should_push)
