"""
GitHub (git) operations for AI-GENT
Branch + commit + push only (no merge)
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
    status = run(["git", "status", "--porcelain"])
    if status:
        raise GitError("Git worktree is not clean; aborting operation")

def current_branch():
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"])

def create_branch(branch_name):
    run(["git", "checkout", "-b", branch_name])

def ensure_ai_branch(branch):
    if not branch.startswith("ai/"):
        raise GitError(f"Refusing to operate on non-AI branch: {branch}")

def branch_exists_remote(branch):
    output = run(["git", "ls-remote", "--heads", "origin", branch])
    return bool(output)

def push_branch(branch):
    run(["git", "push", "-u", "origin", branch])

def write_file(rel_path, content):
    path = REPO_ROOT / rel_path
    path.write_text(content)

def commit_files(message, files):
    run(["git", "add", *files])
    run(["git", "commit", "-m", message])
