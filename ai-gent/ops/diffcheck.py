"""
ai-gent/ops/diffcheck.py — Diff safety checks for AI-GENT
Ensures only declared mes/ files are modified, no binaries, within line bounds
Improved: staged diff, robust parsing, clearer errors
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
    Returns list of (added:int, removed:int, filename:str) for staged changes
    """
    output = run(["git", "diff", "--cached", "--numstat"])
    changes = []
    if not output:
        return changes
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue  # Skip malformed lines gracefully
        added_str, removed_str, path = parts
        # Git uses "-" for binary files
        if added_str == "-" or removed_str == "-":
            raise DiffError(f"Binary or non-text diff detected: {path}")
        try:
            added = int(added_str)
            removed = int(removed_str)
        except ValueError:
            continue  # Skip invalid numbers
        changes.append((added, removed, path))
    return changes

def enforce_diff_safety(update, max_lines: int = 200):
    """
    Safety checks:
    - Only declared mes/* files modified
    - No binary files
    - Total lines changed per file <= max_lines
    """
    declared = update.get("declared_files", [])
    if not declared:
        raise DiffError("No declared_files in update YAML")
    declared_paths = {f"mes/{f}" for f in declared}

    changes = get_diff_numstat()
    if not changes:
        raise DiffError("No staged changes detected; nothing to commit")

    for added, removed, path in changes:
        if path not in declared_paths:
            raise DiffError(f"Unauthorized file modified: {path} (only mes/* declared files allowed)")
        total = added + removed
        if total > max_lines:
            raise DiffError(
                f"Change too large in {path}: {total} lines (+{added}/-{removed}) exceeds limit {max_lines}"
            )
    # All good!