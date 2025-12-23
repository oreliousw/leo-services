#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI-GENT v0.10
Governance + GitOps Steward for leo

Capabilities:
- Validate update intent (AI_RULES.md v2.0 semantics)
- Create or reuse isolated AI branches (Option A)
- Enforce diff safety before commit
- For logic changes: write full-file content
- For parameter changes: in-place parameter patch (no full-file content required)
- CHANGELOG:
    * logic: idempotent stub append using version_to (if provided)
    * parameter: skipped unless version_to is present
- Commit and push safely (no merge)
"""

from __future__ import annotations

import sys
import re
from pathlib import Path
from typing import Dict, Any, Iterable

import yaml

from ops.validate import validate_update, ValidationError
from ops.diffcheck import enforce_diff_safety, DiffError
from ops.github import (
    ensure_clean_worktree,
    current_branch,
    create_or_checkout_branch,   # Option A behavior
    ensure_ai_branch,
    branch_exists_remote,
    write_file,
    commit_files,
    push_branch,
    GitError,
)

REPO_ROOT = Path.home() / "leo-services"
CHANGELOG_PATH = REPO_ROOT / "mes" / "CHANGELOG.md"


# ------------------------------
# YAML load
# ------------------------------
def load_update(path: Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ------------------------------
# Version increment class (legacy compat; harmless if unused)
# ------------------------------
def determine_increment_class(update: dict) -> str:
    """
    Deterministic collapsed precedence per prior VERSION_POLICY.md.
    Retained for compatibility if versioned logic changes keep using it.
    """
    categories = set(update.get("change_categories", []))

    if "strategy_behavior" in categories and "hotfix_critical" in categories:
        return "SUFFIX"

    priority = [
        ("risk_model_rewrite", "MAJOR"),
        ("strategy_behavior", "MINOR"),
        ("execution_logic", "PATCH"),
        ("diagnostics_logging", "PATCH"),
        ("infrastructure", "PATCH"),
        ("hotfix_critical", "SUFFIX"),
    ]
    for cat, cls in priority:
        if cat in categories:
            return cls
    return "PATCH"


# ------------------------------
# CHANGELOG handling
# ------------------------------
def ensure_changelog_entry(update: dict) -> bool:
    """
    Idempotent: appends a stub entry only if version_to is present AND not already in file.
    Returns True if file was modified, False otherwise.
    """
    version = update.get("version_to")
    if not version:
        return False  # parameter-only updates without a bump do not touch changelog

    if CHANGELOG_PATH.exists():
        current_content = CHANGELOG_PATH.read_text(encoding="utf-8")
    else:
        current_content = "# MES Change Log\n\n"
        CHANGELOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    if re.search(rf"^##\s+{re.escape(str(version))}\b", current_content, re.MULTILINE):
        return False

    inc_class = determine_increment_class(update)
    summary = (update.get("summary") or update.get("tagline") or "Automated update").strip()
    if summary and not summary.endswith("."):
        summary += "."

    new_entry = f"\n## {version} ({inc_class})\n- {summary}\n"
    CHANGELOG_PATH.write_text(current_content + new_entry, encoding="utf-8")
    return True


# ------------------------------
# Parameter patching (no full-file content)
# ------------------------------
_PARAM_LINE = re.compile(r"^([A-Z][A-Z0-9_]*\s*=\s*)(.+)$", re.MULTILINE)

def _format_value_for_python(value: Any) -> str:
    """
    Preserve numeric vs string intent. YAML gives us strings for 0.6 etc.
    If it looks like a number, keep it bare; otherwise repr().
    """
    s = str(value).strip()
    # numeric literal? (int/float)
    if re.fullmatch(r"[+-]?(\d+(\.\d*)?|\.\d+)", s):
        return s
    # True/False/None
    if s in {"True", "False", "None"}:
        return s
    return repr(s)

def patch_parameter_in_file(rel_path: str, param: str, new_value: Any) -> bool:
    """
    Replace a top-level assignment like:
        PARAM = <anything>
    with the new value. Returns True if a replacement occurred.
    """
    path = REPO_ROOT / rel_path
    text = path.read_text(encoding="utf-8")

    replaced = False

    def _repl(m: re.Match) -> str:
        nonlocal replaced
        name, _val = m.group(1), m.group(2)
        # Only swap the specific param line
        if text[m.start():m.end()].lstrip().startswith(f"{param}"):
            replaced = True
            return f"{name}{_format_value_for_python(new_value)}"
        return m.group(0)

    new_text = _PARAM_LINE.sub(_repl, text)
    if not replaced:
        raise GitError(f"Parameter '{param}' not found in {rel_path}")

    (REPO_ROOT / rel_path).write_text(new_text, encoding="utf-8")
    return True


# ------------------------------
# UI
# ------------------------------
def usage():
    print("Usage: ai-gent <validate|commit|push> <update.yaml>")
    sys.exit(1)


# ------------------------------
# Main
# ------------------------------
def main():
    if len(sys.argv) < 3:
        usage()

    command = sys.argv[1]
    update_path = Path(sys.argv[2])
    if not update_path.exists():
        print(f"Update file not found: {update_path}")
        sys.exit(1)

    update = load_update(update_path)

    # 1) Validate (new v2.0 validator adds 'content' for legacy logic path, but param path won't use it)
    try:
        validate_update(update)
    except ValidationError as e:
        print(f"Validation failed: {e}")
        sys.exit(1)

    if command == "validate":
        print("Validation passed")
        sys.exit(0)

    # 2) Commit workflow
    if command == "commit":
        try:
            ensure_clean_worktree()

            utype   = update.get("type")
            target  = update["target_file"]
            rel_path = f"mes/{target}"

            # Branch naming:
            # - logic with version_to -> ai/<file>-<version>
            # - parameter -> ai/<file>-param
            version_to = update.get("version_to")
            if utype == "logic" and version_to:
                branch_name = f"ai/{target.replace('.', '-')}-{version_to}"
            else:
                branch_name = f"ai/{target.replace('.', '-')}-param"

            print(f"→ Creating/reusing branch: {branch_name}")
            create_or_checkout_branch(branch_name)  # Option A

            # Apply change
            if utype == "logic":
                # Expect full file content (validator ensured 'content' exists via compat shim)
                content = update["content"]
                write_file(rel_path, content)
            elif utype == "parameter":
                # In-place parameter patch (no full-file content required)
                param = update["parameter"]
                new   = update["change"]["to"]
                patch_parameter_in_file(rel_path, param, new)
            else:
                raise GitError(f"Unknown change type: {utype}")

            # CHANGELOG: only if version bump is provided
            print("→ Ensuring CHANGELOG entry (if version provided)...")
            changelog_modified = ensure_changelog_entry(update)

            # Enforce diff safety BEFORE commit
            enforce_diff_safety(update)

            # Commit message
            msg_head = update.get("tagline") or update.get("statement") or f"{utype} update"
            commit_msg = (
                f"{target} – {msg_head}\n\n"
                f"Change type: {utype}\n"
                f"Managed by AI-GENT"
            )

            files_to_commit: Iterable[str] = [rel_path]
            if changelog_modified:
                files_to_commit = [*files_to_commit, "mes/CHANGELOG.md"]

            commit_files(commit_msg, files_to_commit)
            print("Commit created (CHANGELOG updated only if needed)")
            print(f"Branch: {branch_name}")
            print("ℹ No merge or push performed")
        except (ValidationError, DiffError, GitError, Exception) as e:
            print(f"Commit failed: {e}")
            sys.exit(1)
        sys.exit(0)

    # 3) Push workflow
    if command == "push":
        try:
            ensure_clean_worktree()
            branch = current_branch()
            ensure_ai_branch(branch)
            if branch_exists_remote(branch):
                raise GitError(f"Branch already exists on origin: {branch}")
            print(f"→ Pushing branch to origin: {branch}")
            push_branch(branch)
            print("Branch pushed successfully")
            print("ℹ No merge performed")
        except (GitError, Exception) as e:
            print(f"Push failed: {e}")
            sys.exit(1)
        sys.exit(0)

    usage()


if __name__ == "__main__":
    main()
