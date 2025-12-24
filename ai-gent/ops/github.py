"""
GitHub (git) operations for AI-GENT
Branch + commit + push only (no merge)
Updated: modern commands, quiet mode, pre-push safety check
"""
import subprocess
from pathlib import Path

REPO_ROOT = Path.home() / "leo-services"

class GitError(Exception):
    pass

def run(cmd):
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GitError(result.stderr.strip())
    return result.stdout.strip()

def ensure_clean_worktree():
    status = run(["git", "status", "--porcelain", "--quiet"])
    if status:
        raise GitError("Git worktree is not clean; aborting operation")

def current_branch():
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"])

def create_branch(branch_name):
    # Modern preferred command
    run(["git", "switch", "-c", branch_name])

def ensure_ai_branch(branch):
    if not branch.startswith("ai/"):
        raise GitError(f"Refusing to operate on non-AI branch: {branch}")

def branch_exists_remote(branch):
    output = run(["git", "ls-remote", "--heads", "origin", branch])
    return bool(output)

def push_branch(branch):
    # Safety: if remote branch exists with different history, fail early
    if branch_exists_remote(branch):
        local_sha = run(["git", "rev-parse", branch])
        remote_sha = run(["git", "rev-parse", f"origin/{branch}"])
        if local_sha != remote_sha:
            raise GitError(
                f"Remote branch {branch} exists with different history. "
                "Pull/merge manually first."
            )
    # Quiet push with upstream tracking
    run(["git", "push", "--quiet", "-u", "origin", branch])

def write_file(rel_path, content):
    path = REPO_ROOT / rel_path
    path.write_text(content)

def commit_files(message, files):
    run(["git", "add", "--quiet", *files])
    run(["git", "commit", "--quiet", "-m", message])