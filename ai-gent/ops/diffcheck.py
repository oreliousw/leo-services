"""
Diff safety checks for AI-GENT
Ensures only declared files are modified and within safe bounds
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path.home() / "leo-services"


class DiffError(Exception):
    pass


def run(cmd):
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DiffError(result.stderr.strip())
    return result.stdout.strip()


def get_diff_numstat():
    """
    Returns list of (added, removed, filename)
    """
    output = run(["git", "diff", "--numstat"])
    changes = []

    if not output:
        return changes

    for line in output.splitlines():
        added, removed, path = line.split("\t")
        changes.append((added, removed, path))

    return changes


def enforce_diff_safety(update, max_lines=200):
    declared = update.get("declared_files", [])
    declared_paths = {f"mes/{f}" for f in declared}

    changes = get_diff_numstat()

    if not changes:
        raise DiffError("No changes detected; nothing to commit")

    for added, removed, path in changes:
        # File scope check
        if path not in declared_paths:
            raise DiffError(f"Unauthorized file modified: {path}")

        # Deletion check
        if added == "-" or removed == "-":
            raise DiffError(f"Binary or non-text diff detected: {path}")

        total = int(added) + int(removed)
        if total > max_lines:
            raise DiffError(
                f"Change too large in {path}: {total} lines (limit {max_lines})"
            )

