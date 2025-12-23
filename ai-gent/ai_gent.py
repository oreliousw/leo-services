#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Git operations for AI-GENT
==========================

Single-responsibility helpers to interact with the leo-services repo.

Key behavior (Option A):
- If a target branch already exists (local or remote), reuse it (checkout).
- If it does not exist, create it (checkout -b).
- Never operate on non "ai/*" branches via these helpers.

This file is designed to be boring and predictable. No merges, no rebases,
no history rewriting — just branch, write, commit, push.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable, Union, List

# Repository root where AI-GENT operates
REPO_ROOT = Path.home() / "leo-services"


class GitError(Exception):
    """Raised when a git command fails."""


def run(cmd: List[str]) -> str:
    """
    Run a git (or shell) command in REPO_ROOT and return stdout as text.
    Raises GitError on non-zero exit.
    """
    result = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        stdout = (result.stdout or "").strip()
        msg = stderr if stderr else stdout if stdout else f"Command failed: {' '.join(cmd)}"
        raise GitError(msg)
    return (result.stdout or "").strip()


def ensure_clean_worktree() -> None:
    """
    Fail if there are unstaged/uncommitted changes. We require a clean tree
    before AI-GENT writes or commits files.
    """
    status = run(["git", "status", "--porcelain"])
    if status:
        raise GitError("Git worktree is not clean; aborting operation")


def current_branch() -> str:
    """Return the current branch name."""
    return run(["git", "rev-parse", "--abbrev-ref", "HEAD"])


def ensure_ai_branch(branch: str) -> None:
    """
    Guardrail: only allow AI-GENT helpers to operate on ai/* branches.
    """
    if not branch.startswith("ai/"):
        raise GitError(f"Refusing to operate on non-AI branch: {branch}")


def branch_exists_local(branch: str) -> bool:
    """
    True if a local branch with this name exists.
    """
    output = run(["git", "branch", "--list", branch])
    return bool(output)


def branch_exists_remote(branch: str) -> bool:
    """
    True if a remote branch (origin/<branch>) exists.
    """
    output = run(["git", "ls-remote", "--heads", "origin", branch])
    return bool(output)


def create_or_checkout_branch(branch_name: str) -> None:
    """
    Option A behavior:
      - If already on the requested branch, do nothing.
      - If the branch exists locally: checkout it.
      - Else if it exists remotely: checkout tracking origin/<branch>.
      - Else: create it locally with 'checkout -b'.
    """
    ensure_ai_branch(branch_name)

    curr = current_branch()
    if curr == branch_name:
        return  # already on target

    if branch_exists_local(branch_name):
        run(["git", "checkout", branch_name])
        return

    if branch_exists_remote(branch_name):
        run(["git", "checkout", "-t", f"origin/{branch_name}"])
        return

    run(["git", "checkout", "-b", branch_name])


# Backward-compat wrapper: some callers still import/create_branch(...)
def create_branch(branch_name: str) -> None:
    """
    Backwards-compatible alias for create_or_checkout_branch.
    Safe to remove once all callers are migrated.
    """
    create_or_checkout_branch(branch_name)


def push_branch(branch: str) -> None:
    """
    Push current branch to origin, setting upstream if needed.
    """
    ensure_ai_branch(branch)
    run(["git", "push", "-u", "origin", branch])


def write_file(rel_path: Union[str, Path], content: str) -> None:
    """
    Write file contents under REPO_ROOT, creating parent dirs as needed.
    """
    path = REPO_ROOT / Path(rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def commit_files(message: str, files: Iterable[Union[str, Path]]) -> None:
    """
    Stage and commit the provided files with the given commit message.
    """
    file_args = [str(REPO_ROOT / Path(f)) for f in files]
    run(["git", "add", *file_args])
    run(["git", "commit", "-m", message])
