"""Stage 2 & 4: Version Tag Calculation & Git Tag Management.

- Fetches latest git tag
- Increments version: patch increment with 9 -> 0 rollover (e.g., 1.0.9 -> 1.1.0)
- Creates git tag locally and records it to ci/.version
- Pushes git tag to GitHub repository
- Creates a formal GitHub Release with auto-generated release notes
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

VERSION_FILE = Path(__file__).parent / ".version"


def get_latest_tag() -> str:
    """Fetch the latest tag from git sorted by version refname."""
    try:
        subprocess.run(
            ["git", "fetch", "--tags"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        result = subprocess.run(
            ["git", "tag", "-l", "--sort=-v:refname"],
            capture_output=True,
            text=True,
            check=True,
        )
        tags = [t.strip() for t in result.stdout.splitlines() if t.strip()]
        if tags:
            return tags[0]
    except Exception as e:
        print(f"Warning: Could not fetch git tags: {e}", file=sys.stderr)

    return "v1.0.0"


def increment_tag(tag: str) -> str:
    """Increment tag: e.g. 1.0.0 -> 1.0.1, 1.0.8 -> 1.0.9, 1.0.9 -> 1.1.0."""
    if not tag:
        return "v1.0.0"

    prefix = "v" if tag.startswith("v") else ""
    clean = tag.lstrip("v")

    parts: list[int] = []
    for part in clean.split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)

    while len(parts) < 3:
        parts.append(0)

    major, minor, patch = parts[0], parts[1], parts[2]

    # Rollover logic: 1.0.9 -> 1.1.0; 1.9.9 -> 2.0.0
    if patch >= 9:
        patch = 0
        minor += 1
        if minor >= 10:
            minor = 0
            major += 1
    else:
        patch += 1

    return f"{prefix}{major}.{minor}.{patch}"


def save_version(new_tag: str) -> None:
    VERSION_FILE.write_text(new_tag.strip(), encoding="utf-8")
    if "GITHUB_OUTPUT" in os.environ:
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as f:
            f.write(f"new_tag={new_tag.strip()}\n")


def load_version() -> str:
    if os.environ.get("NEW_TAG", "").strip():
        return os.environ["NEW_TAG"].strip()
    if VERSION_FILE.exists():
        return VERSION_FILE.read_text(encoding="utf-8").strip()
    return ""


def ensure_git_config() -> None:
    res = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True)
    if not res.stdout.strip():
        subprocess.run(["git", "config", "--global", "user.name", "github-actions[bot]"], check=False)
        subprocess.run(["git", "config", "--global", "user.email", "github-actions[bot]@users.noreply.github.com"], check=False)


def create_git_tag(tag: str) -> None:
    ensure_git_config()
    check = subprocess.run(["git", "tag", "-l", tag], capture_output=True, text=True)
    if tag in check.stdout.split():
        print(f"Tag {tag} already exists locally.")
        return
    print(f"Creating local git tag: {tag}")
    subprocess.run(["git", "tag", "-a", tag, "-m", f"Release {tag}"], check=True)


def push_git_tag(tag: str) -> None:
    create_git_tag(tag)
    print(f"Pushing git tag to remote: {tag}")
    subprocess.run(["git", "push", "origin", tag], check=True)


def create_github_release(tag: str) -> None:
    """Create a formal GitHub Release so the tag shows on the repository's Releases page."""
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("Note: GITHUB_TOKEN not set. Tag pushed, but GitHub Release creation skipped.")
        return

    print(f"Creating formal GitHub Release for {tag}...")

    # 1. Try using the GitHub CLI (gh) pre-installed on GitHub Actions runners
    try:
        env = {**os.environ, "GH_TOKEN": token}
        subprocess.run(
            ["gh", "release", "create", tag, "--title", f"Release {tag}", "--generate-notes"],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"GitHub Release '{tag}' successfully created via gh CLI!")
        return
    except Exception:
        pass

    # 2. Fallback: Direct GitHub REST API call
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        try:
            import json
            import urllib.request

            url = f"https://api.github.com/repos/{repo}/releases"
            payload = json.dumps({
                "tag_name": tag,
                "name": f"Release {tag}",
                "generate_release_notes": True,
            }).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "SeniorLifeOS-CI",
                },
            )
            with urllib.request.urlopen(req) as resp:
                if resp.status in (200, 201):
                    print(f"GitHub Release '{tag}' successfully created via REST API!")
        except Exception as e:
            print(f"Warning: Could not create GitHub Release: {e}", file=sys.stderr)


def main() -> None:
    args = sys.argv[1:]

    # Stage 4: Push tag to remote & create GitHub Release
    if "--push" in args or "--push-only" in args:
        tag = load_version()
        if not tag:
            print("Error: No version tag found to push. Run tag creation first.", file=sys.stderr)
            sys.exit(1)
        print("=" * 60)
        print(f"[CI STAGE 4: GIT PUSH & RELEASE] Pushing tag {tag} & Creating Release")
        print("=" * 60)
        push_git_tag(tag)
        create_github_release(tag)
        print(f"[CI STAGE 4: SUCCESS] Tag {tag} pushed and GitHub Release created!\n")
        return

    # Stage 2: Calculate and create tag locally
    print("=" * 60)
    print("[CI STAGE 2: GIT TAG] Determining Next Version")
    print("=" * 60)

    latest = get_latest_tag()
    new_tag = increment_tag(latest)
    print(f"Latest tag:     {latest}")
    print(f"Next increment: {new_tag}")

    save_version(new_tag)
    print(f"[CI STAGE 2: SUCCESS] Next version determined: {new_tag} (saved to GITHUB_OUTPUT)\n")


if __name__ == "__main__":
    main()
