"""Create GitHub labels, milestones, and issues from docs/backlog.yaml.

Requires the GitHub CLI (`gh`) logged in with access to the repo.
Idempotent: labels are created or updated, and milestones and issues that
already exist (matched by exact title) are skipped.

Usage:
    uv run python scripts/create_issues.py --dry-run
    uv run python scripts/create_issues.py
    uv run python scripts/create_issues.py --repo owner/name --backlog docs/backlog.yaml
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

DEFAULT_BACKLOG = Path(__file__).resolve().parents[1] / "docs" / "backlog.yaml"


def gh(*args: str, stdin: str | None = None) -> str:
    """Run a gh command and return stdout; exit with a clear message on failure."""
    result = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.exit(f"gh {' '.join(args[:3])} ... failed:\n{result.stderr.strip()}")
    return result.stdout


def detect_repo() -> str:
    return gh("repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner").strip()


def load_backlog(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    for key in ("labels", "milestones", "issues"):
        data.setdefault(key, [])
    label_names = {label["name"] for label in data["labels"]}
    milestone_titles = {m["title"] for m in data["milestones"]}
    titles: set[str] = set()
    for issue in data["issues"]:
        title = issue["title"]
        if title in titles:
            sys.exit(f"duplicate issue title in backlog: {title}")
        titles.add(title)
        unknown = set(issue.get("labels", [])) - label_names
        if unknown:
            sys.exit(f"issue '{title}' uses undefined labels: {sorted(unknown)}")
        if issue.get("milestone") and issue["milestone"] not in milestone_titles:
            sys.exit(f"issue '{title}' uses undefined milestone: {issue['milestone']}")
    return data


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", help="owner/name (default: the repo in the current directory)")
    parser.add_argument("--backlog", type=Path, default=DEFAULT_BACKLOG)
    parser.add_argument("--dry-run", action="store_true", help="print what would be created")
    args = parser.parse_args()

    backlog = load_backlog(args.backlog)

    if args.dry_run:
        repo = args.repo or "<current repo>"
        print(f"[dry run] repo: {repo}")
        print(
            f"[dry run] {len(backlog['labels'])} labels, "
            f"{len(backlog['milestones'])} milestones, {len(backlog['issues'])} issues"
        )
        for issue in backlog["issues"]:
            print(
                f"  - [{issue.get('milestone', '-')}] {issue['title']}  "
                f"({', '.join(issue.get('labels', []))})"
            )
        return

    if shutil.which("gh") is None:
        sys.exit("GitHub CLI not found. Install it (brew install gh) and run: gh auth login")
    repo = args.repo or detect_repo()
    print(f"repo: {repo}")

    # Labels: --force creates or updates.
    for label in backlog["labels"]:
        gh(
            "label",
            "create",
            label["name"],
            "--repo",
            repo,
            "--color",
            label["color"],
            "--description",
            label.get("description", ""),
            "--force",
        )
    print(f"labels: {len(backlog['labels'])} ensured")

    # Milestones: create only missing ones.
    existing_ms = json.loads(gh("api", f"repos/{repo}/milestones?state=all&per_page=100"))
    existing_ms_titles = {m["title"] for m in existing_ms}
    created_ms = 0
    for milestone in backlog["milestones"]:
        if milestone["title"] in existing_ms_titles:
            continue
        gh(
            "api",
            f"repos/{repo}/milestones",
            "-f",
            f"title={milestone['title']}",
            "-f",
            f"description={milestone.get('description', '')}",
        )
        created_ms += 1
    print(f"milestones: {created_ms} created, {len(existing_ms_titles)} already existed")

    # Issues: skip titles that already exist (open or closed).
    existing_issues = json.loads(
        gh("issue", "list", "--repo", repo, "--state", "all", "--limit", "1000", "--json", "title")
    )
    existing_titles = {issue["title"] for issue in existing_issues}
    created = skipped = 0
    for issue in backlog["issues"]:
        if issue["title"] in existing_titles:
            skipped += 1
            continue
        cmd = ["issue", "create", "--repo", repo, "--title", issue["title"], "--body-file", "-"]
        for label in issue.get("labels", []):
            cmd += ["--label", label]
        if issue.get("milestone"):
            cmd += ["--milestone", issue["milestone"]]
        url = gh(*cmd, stdin=issue.get("body", "")).strip()
        print(f"created: {url}  {issue['title']}")
        created += 1
    print(f"issues: {created} created, {skipped} already existed")


if __name__ == "__main__":
    main()
